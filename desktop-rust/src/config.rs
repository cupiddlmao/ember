use std::env;
use std::fmt;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BridgeConfig {
    checkout: PathBuf,
    python: PathBuf,
    bridge: PathBuf,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ConfigError {
    message: String,
}

impl ConfigError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl fmt::Display for ConfigError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for ConfigError {}

impl BridgeConfig {
    /// Finds the checkout from an explicit environment override or from the
    /// executable/source location. The process working directory is irrelevant.
    pub fn discover() -> Result<Self, ConfigError> {
        if let Some(value) = env::var_os("EMBER_CHECKOUT_ROOT") {
            if value.is_empty() {
                return Err(ConfigError::new(
                    "EMBER_CHECKOUT_ROOT is empty. Point it at the Ember checkout.",
                ));
            }
            return Self::from_root(PathBuf::from(value)).map_err(|error| {
                ConfigError::new(format!("EMBER_CHECKOUT_ROOT is not usable: {error}"))
            });
        }

        let mut candidates = Vec::new();
        if let Ok(executable) = env::current_exe() {
            candidates.extend(executable.ancestors().skip(1).map(Path::to_path_buf));
        }
        candidates.extend(
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .ancestors()
                .map(Path::to_path_buf),
        );
        candidates.dedup();

        for candidate in candidates {
            if let Ok(config) = Self::from_root(candidate) {
                return Ok(config);
            }
        }

        Err(ConfigError::new(
            "Could not find the Ember checkout. Run from the checkout, or set \
             EMBER_CHECKOUT_ROOT to its absolute path.",
        ))
    }

    pub fn from_root(root: impl AsRef<Path>) -> Result<Self, ConfigError> {
        let root = fs::canonicalize(root.as_ref())
            .map_err(|_| ConfigError::new("The configured Ember checkout path does not exist."))?;
        if !root.join("music.py").is_file() {
            return Err(ConfigError::new(
                "The configured checkout does not contain music.py.",
            ));
        }
        let python = root.join(".venv/bin/python");
        if !python.is_file() {
            return Err(ConfigError::new(format!(
                "The checkout Python environment is missing at {}. Run `bash run.sh` once from the checkout.",
                python.display()
            )));
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let executable = fs::metadata(&python)
                .map(|metadata| metadata.permissions().mode() & 0o111 != 0)
                .unwrap_or(false);
            if !executable {
                return Err(ConfigError::new(format!(
                    "The checkout Python environment is not executable at {}.",
                    python.display()
                )));
            }
        }
        let bridge = root.join("desktop-rust/bridge.py");
        if !bridge.is_file() {
            return Err(ConfigError::new(format!(
                "The Rust Python bridge is missing at {}.",
                bridge.display()
            )));
        }
        Ok(Self {
            checkout: root,
            python,
            bridge,
        })
    }

    /// Builds an argument-only command. No shell is involved and the child
    /// starts in the discovered checkout rather than the launch directory.
    pub fn command(&self) -> Command {
        let mut command = Command::new(&self.python);
        command
            .arg(&self.bridge)
            .arg("--root")
            .arg(&self.checkout)
            .current_dir(&self.checkout);
        command
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn fake_checkout() -> tempfile::TempDir {
        let root = tempfile::tempdir().expect("temp checkout");
        fs::write(
            root.path().join("music.py"),
            "def search(query):\n    return {}\n",
        )
        .expect("music.py");
        let bridge_dir = root.path().join("desktop-rust");
        fs::create_dir_all(&bridge_dir).expect("bridge dir");
        fs::write(bridge_dir.join("bridge.py"), "#!/usr/bin/env python3\n").expect("bridge");
        let bin = root.path().join(".venv/bin");
        fs::create_dir_all(&bin).expect("venv dir");
        let python = bin.join("python");
        let mut file = fs::File::create(&python).expect("python file");
        writeln!(file, "#!/bin/sh\nexit 0").expect("python script");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&python, fs::Permissions::from_mode(0o755))
                .expect("python executable");
        }
        root
    }

    #[test]
    fn command_uses_explicit_paths_and_checkout_working_directory() {
        let root = fake_checkout();
        let config = BridgeConfig::from_root(root.path()).expect("valid fake checkout");
        let command = config.command();
        let args: Vec<String> = command
            .get_args()
            .map(|arg| arg.to_string_lossy().into_owned())
            .collect();
        assert_eq!(
            args,
            vec![
                config.bridge.to_string_lossy().into_owned(),
                "--root".to_owned(),
                config.checkout.to_string_lossy().into_owned(),
            ]
        );
        assert_eq!(command.get_current_dir(), Some(config.checkout.as_path()));
        assert_eq!(
            command.get_program().to_string_lossy(),
            config.python.to_string_lossy()
        );
    }

    #[test]
    fn rejects_a_directory_without_music_py() {
        let root = tempfile::tempdir().expect("temp dir");
        let error = BridgeConfig::from_root(root.path()).expect_err("must reject empty root");
        assert!(error.to_string().contains("music.py"));
    }
}
