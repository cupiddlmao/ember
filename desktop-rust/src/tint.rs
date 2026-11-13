//! Palette extraction for the two artwork-led surfaces.
//!
//! A tint is sampled from decoded cover pixels, then clamped against the
//! primary text colour before it is emitted as CSS. The gradients stay at or
//! below the strengths in ADR 0002: 35% for the playlist hero and 25% for
//! immersive lyrics.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Tint {
    pub base: [u8; 3],
    pub accent: [u8; 3],
}

const SAMPLE_EVERY: usize = 16;
const ALPHA_FLOOR: u8 = 128;
const BIN_COUNT: usize = 16 * 16 * 16;
const ACCENT_MIN_CHROMA: u8 = 24;
const TEXT: [u8; 3] = [244, 244, 246];
const TEXT_CONTRAST: f64 = 4.5;
const HERO_STRENGTH: f64 = 0.35;
const HERO_ACCENT_STRENGTH: f64 = 0.18;
const IMMERSIVE_STRENGTH: f64 = 0.25;
const IMMERSIVE_ACCENT_STRENGTH: f64 = 0.12;

/// Samples every 16th pixel, ignores transparent pixels, and returns the most
/// common opaque colour plus the most prominent saturated colour.
pub fn from_rgba(pixels: &[u8], width: u32, height: u32) -> Option<Tint> {
    let pixel_count = (width as usize).checked_mul(height as usize)?;
    if pixel_count == 0 || pixels.len() < pixel_count.checked_mul(4)? {
        return None;
    }

    let mut counts = [0u32; BIN_COUNT];
    let mut sums = [[0u64; 3]; BIN_COUNT];
    for pixel in (0..pixel_count).step_by(SAMPLE_EVERY) {
        let offset = pixel * 4;
        let sample = &pixels[offset..offset + 4];
        if sample[3] < ALPHA_FLOOR {
            continue;
        }
        let bin = ((usize::from(sample[0]) >> 4) << 8)
            | ((usize::from(sample[1]) >> 4) << 4)
            | (usize::from(sample[2]) >> 4);
        counts[bin] += 1;
        for channel in 0..3 {
            sums[bin][channel] += u64::from(sample[channel]);
        }
    }

    let base_bin = (0..BIN_COUNT).max_by_key(|&bin| counts[bin])?;
    if counts[base_bin] == 0 {
        return None;
    }
    let base = average(&sums[base_bin], counts[base_bin]);

    let mut accent = None;
    let mut accent_score = 0u64;
    for bin in 0..BIN_COUNT {
        if counts[bin] == 0 || bin == base_bin {
            continue;
        }
        let colour = average(&sums[bin], counts[bin]);
        let chroma = *colour.iter().max()? - *colour.iter().min()?;
        if chroma < ACCENT_MIN_CHROMA {
            continue;
        }
        let score = u64::from(counts[bin]) * u64::from(chroma);
        if accent.is_none() || score > accent_score {
            accent = Some(colour);
            accent_score = score;
        }
    }

    Some(Tint {
        base,
        accent: accent.unwrap_or(base),
    })
}

fn average(sum: &[u64; 3], count: u32) -> [u8; 3] {
    let count = u64::from(count);
    [
        (sum[0] / count) as u8,
        (sum[1] / count) as u8,
        (sum[2] / count) as u8,
    ]
}

fn linear(channel: u8) -> f64 {
    let channel = f64::from(channel) / 255.0;
    if channel <= 0.04045 {
        channel / 12.92
    } else {
        ((channel + 0.055) / 1.055).powf(2.4)
    }
}

fn luminance(colour: [u8; 3]) -> f64 {
    0.2126 * linear(colour[0]) + 0.7152 * linear(colour[1]) + 0.0722 * linear(colour[2])
}

pub fn contrast_ratio(a: [u8; 3], b: [u8; 3]) -> f64 {
    let (lighter, darker) = if luminance(a) >= luminance(b) {
        (luminance(a), luminance(b))
    } else {
        (luminance(b), luminance(a))
    };
    (lighter + 0.05) / (darker + 0.05)
}

fn mix(from: [u8; 3], to: [u8; 3], amount: f64) -> [u8; 3] {
    let channel = |index: usize| {
        (f64::from(from[index]) + (f64::from(to[index]) - f64::from(from[index])) * amount)
            .round()
            .clamp(0.0, 255.0) as u8
    };
    [channel(0), channel(1), channel(2)]
}

/// Moves a colour toward black or white until it clears `min` against
/// `background`. The search is bounded to 100 steps in each direction.
pub fn clamp_for_contrast(colour: [u8; 3], background: [u8; 3], min: f64) -> [u8; 3] {
    if contrast_ratio(colour, background) >= min {
        return colour;
    }
    for step in 1..=100 {
        let candidate = mix(colour, [0, 0, 0], f64::from(step) / 100.0);
        if contrast_ratio(candidate, background) >= min {
            return candidate;
        }
    }
    for step in 1..=100 {
        let candidate = mix(colour, [255, 255, 255], f64::from(step) / 100.0);
        if contrast_ratio(candidate, background) >= min {
            return candidate;
        }
    }
    if contrast_ratio([0, 0, 0], background) >= contrast_ratio([255, 255, 255], background) {
        [0, 0, 0]
    } else {
        [255, 255, 255]
    }
}

fn rgba(colour: [u8; 3], alpha: f64) -> String {
    format!(
        "rgba({}, {}, {}, {alpha:.2})",
        colour[0], colour[1], colour[2]
    )
}

/// CSS for the playlist hero. Emitting only this surface's selector keeps a
/// display-level provider from tinting the immersive layer as well.
pub fn hero_css(tint: &Tint) -> String {
    gradient(".tint-hero", tint, HERO_STRENGTH, HERO_ACCENT_STRENGTH)
}

/// CSS for the immersive lyrics surface.
pub fn immersive_css(tint: &Tint) -> String {
    gradient(
        ".tint-immersive",
        tint,
        IMMERSIVE_STRENGTH,
        IMMERSIVE_ACCENT_STRENGTH,
    )
}

fn gradient(selector: &str, tint: &Tint, strength: f64, accent_strength: f64) -> String {
    let base = clamp_for_contrast(tint.base, TEXT, TEXT_CONTRAST);
    let accent = clamp_for_contrast(tint.accent, TEXT, TEXT_CONTRAST);
    format!(
        "{selector} {{\n\
         \x20 background-color: transparent;\n\
         \x20 background-image: linear-gradient(160deg, {from} 0%, {via} 46%, {to} 100%);\n\
         }}\n",
        from = rgba(base, strength),
        via = rgba(accent, accent_strength),
        to = rgba(base, 0.0),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn contrast_is_standard_and_clamped() {
        assert!((contrast_ratio([255, 255, 255], [0, 0, 0]) - 21.0).abs() < 0.1);
        let clamped = clamp_for_contrast([250, 245, 180], [27, 27, 31], 4.5);
        assert!(contrast_ratio(clamped, [27, 27, 31]) >= 4.5);
    }

    #[test]
    fn empty_or_transparent_pixels_produce_no_tint() {
        assert!(from_rgba(&[], 0, 0).is_none());
        assert!(from_rgba(&[0, 0, 0, 0], 1, 1).is_none());
        assert!(from_rgba(&[255, 0, 0, 255], 1, 1).is_some());
    }

    #[test]
    fn invalid_pixel_buffers_are_rejected() {
        assert!(from_rgba(&[255, 0, 0, 255], 2, 1).is_none());
        assert!(from_rgba(&[255, 0, 0, 255], 0, 1).is_none());
    }

    #[test]
    fn a_bright_colour_is_darkened_for_text_contrast() {
        let clamped = clamp_for_contrast([255, 255, 0], TEXT, TEXT_CONTRAST);
        assert!(contrast_ratio(clamped, TEXT) >= TEXT_CONTRAST);
        assert_ne!(clamped, [255, 255, 0]);
    }

    #[test]
    fn near_black_covers_keep_a_dark_tint() {
        let tint = from_rgba(&[18, 18, 22, 255], 1, 1).expect("opaque pixel");
        assert_eq!(tint.base, [18, 18, 22]);
        assert_eq!(clamp_for_contrast(tint.base, TEXT, TEXT_CONTRAST), tint.base);
        assert!(contrast_ratio(tint.base, TEXT) >= TEXT_CONTRAST);
    }

    #[test]
    fn each_surface_gets_only_its_own_bounded_gradient() {
        let tint = Tint {
            base: [255, 255, 255],
            accent: [255, 0, 0],
        };
        let hero = hero_css(&tint);
        assert!(hero.contains("160deg"));
        assert!(hero.contains("0.35"));
        assert!(!hero.contains("0.25"));
        assert!(!hero.contains(".tint-immersive"));

        let immersive = immersive_css(&tint);
        assert!(immersive.contains("160deg"));
        assert!(immersive.contains("0.25"));
        assert!(!immersive.contains("0.36"));
        assert!(!immersive.contains(".tint-hero"));
    }
}
