//! Motion tokens for the durations Rust drives directly.
//!
//! Hover, press and selection transitions are declared in `style.css` beside
//! the selectors that use them; these tokens cover the transitions Rust drives
//! itself (panel reveal, widget stack, tint crossfade, immersive entry). Every
//! animation has an instant path when GTK animations are disabled.

use std::time::Duration;

/// Panel and page reveals.
pub const PAGE: Duration = Duration::from_millis(220);
/// Widget stack crossfades. `style.css` uses the same 180 ms for row
/// selection, which GTK CSS cannot read from here.
pub const STATE: Duration = Duration::from_millis(180);
/// Artwork tint crossfade on the immersive surface.
pub const TINT: Duration = Duration::from_millis(320);
/// Immersive lyrics window entry.
pub const IMMERSIVE: Duration = Duration::from_millis(420);

/// `cubic-bezier(.2, 0, 0, 1)` evaluated as a timing function.
pub fn ease(t: f64) -> f64 {
    if t.is_nan() {
        return 0.0;
    }
    let t = t.clamp(0.0, 1.0);
    if t == 0.0 || t == 1.0 {
        return t;
    }

    // Solve x(u) = t for u, then evaluate y(u). Both curves are monotonic for
    // these control points, so Newton's method converges in a few steps.
    let mut u = t;
    for _ in 0..8 {
        let x = 0.6 * u * (1.0 - u) * (1.0 - u) + u * u * u;
        let dx = 0.6 * (1.0 - u) * (1.0 - u) - 1.2 * u * (1.0 - u) + 3.0 * u * u;
        if dx.abs() < 1e-9 {
            break;
        }
        u = (u - (x - t) / dx).clamp(0.0, 1.0);
    }
    let y = 3.0 * (1.0 - u) * u * u + u * u * u;
    y.clamp(0.0, 1.0)
}

/// Eased progress for an animation, or `1.0` when animation is disabled.
pub fn eased_progress(elapsed: Duration, duration: Duration, animations: bool) -> f64 {
    if !animations || duration.is_zero() {
        return 1.0;
    }
    ease(elapsed.as_secs_f64() / duration.as_secs_f64())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn easing_is_clamped_monotonic_and_exact_at_ends() {
        assert_eq!(ease(-1.0), 0.0);
        assert_eq!(ease(0.0), 0.0);
        assert_eq!(ease(1.0), 1.0);
        assert_eq!(ease(2.0), 1.0);
        let mut last = 0.0;
        for step in 0..=100 {
            let value = ease(step as f64 / 100.0);
            assert!(value >= last);
            last = value;
        }
    }

    #[test]
    fn disabled_animation_jumps_to_end() {
        let half = Duration::from_millis(110);
        let full = Duration::from_millis(220);
        assert_eq!(eased_progress(half, full, false), 1.0);
        assert_eq!(eased_progress(full, full, true), 1.0);
        assert_eq!(eased_progress(Duration::ZERO, full, true), 0.0);
    }
}
