Run
chmod +x luminafix.py
./luminafix.py
✦ Usage Flow
Detect Tab
Scan for connected displays
View hardware + EDID metadata
Profile Tab
Inspect computed severity metrics:
Backlight bleed
Gradient uniformity
Clouding
Review panel notes
Correct Tab
Apply recommended correction
Adjust sliders if needed
Enable/disable overlay in real-time
✦ Architecture Overview
EDID → Panel Detection → Profile Engine → Technique Selection
↓
Correction Pipeline
(Gamma + Overlay Rendering)
✦ Key Algorithms

1. PPI-Normalized Bleed Visibility

Higher pixel density reduces perceived bleed:

weight = REF_PPI / actual_PPI

Clamped to [0.5, 1.5]

2. Severity Model

Balanced composite + worst-case:

severity = 0.5 _ composite + 0.5 _ max_metric

Prevents catastrophic issues from being averaged out.

3. Overlay Alpha (Gamma-Correct)

Correct alpha in gamma space:

α = 1 − (1 − L_target)^(1/γ) 4. Exponential Edge Falloff

Physically realistic bleed decay:

I(t) = α _ exp(-k _ t) 5. Color Temperature Compensation
Converts Kelvin bias → RGB gamma offsets
Uses:
CIE XYZ transform
Linear RGB mapping
Mid-gray gamma inversion
✦ Configuration

Stored at:

~/.config/luminafix/profiles.json

Used for:

Persisting user adjustments
Reapplying preferred settings
✦ Limitations
Requires X11 (xrandr) — Wayland not supported
Gamma control may fail on:
Some HDMI outputs
Modesetting drivers
Overlay depends on compositor support (RGBA visuals)
✦ Future Roadmap
Wayland backend (wlroots / gamma-control protocol)
ICC profile integration
Real-time luminance sampling (camera-assisted calibration)
Machine-learned panel classification
✦ License

MIT License

✦ Philosophy

LuminaFix treats display correction as a perceptual physics problem, not just a visual tweak:

Model the panel
Normalize for viewing conditions
Apply physically meaningful corrections
✦ Author Notes

Designed for:

Engineers
Designers
Anyone bothered by uneven panels

If you’ve ever noticed glow in the corners during dark scenes — this is for you.

---

# 📄 `docs.md`

````markdown
# LuminaFix — Technical Documentation

---

# 1. System Overview

LuminaFix is a hybrid system combining:

- Hardware introspection (EDID parsing)
- Empirical panel profiling
- Perceptual modeling
- Real-time graphical correction

---

# 2. Display Detection Pipeline

## 2.1 EDID Parsing

Source:

/sys/class/drm/\*/edid

Extracted fields:

- Manufacturer code (3-char)
- Product ID
- Resolution
- Physical size (mm)
- Gamma
- Interface type

---

## 2.2 xrandr Reconciliation

Problem:

- Kernel names ≠ xrandr names

Solution:

- Normalize identifiers
- Apply fuzzy matching
- Resolve variants:
  - `HDMI-A-1` ↔ `HDMI-1`
  - `DP-1` ↔ `DP1`

---

# 3. Panel Profiling Engine

## 3.1 Knowledge Base

Each manufacturer mapped to:

```python
{
  bleed: float,
  gradient: float,
  clouding: float,
  ct_bias: int
}
3.2 PPI Normalization

Bleed visibility is angular:

ppi = diagonal_pixels / diagonal_inches
weight = REF_PPI / ppi
3.3 Severity Calculation
composite = 0.5*bleed + 0.3*gradient + 0.2*clouding
severity  = 0.5*composite + 0.5*max_metric
4. Correction System
4.1 Gamma Adjustment

Uses:

xrandr --brightness B --gamma R:G:B
Brightness Mapping (CIE L*)
Y = ((L* + 16) / 116)^3
brightness = Y^(1/γ)
4.2 Color Temperature Compensation

Pipeline:

CCT → xy chromaticity → XYZ → linear RGB → gamma offsets

Clamp:

Δγ ∈ [-0.15, 0.15]
4.3 Overlay Rendering
Edge Mask
Linear gradients per edge
Exponential decay
I(t) = α * exp(-k * t)
Corner Correction

Inclusion-exclusion:

α_corner = 2f(t) - f(t)^2
Radial Uniformity

Raised cosine:

α(r) = α * (1 - cos(πr)) / 2
5. Technique Selection Logic
Severity	Technique
≥ 0.60	Combined
≥ 0.40	Edge Mask
≥ 0.20	Gamma Only
< 0.20	Minimal
6. GTK Overlay Window

Properties:

RGBA visual
Click-through (input shape)
Always on top
Per-monitor positioning
7. Performance Considerations
EDID parsing: negligible
xrandr calls: ~5ms
Cairo rendering: GPU-accelerated via compositor
Overlay updates: event-driven
8. Failure Modes
8.1 Gamma Unsupported

Occurs on:

HDMI outputs (driver-dependent)
Modesetting backend

Fallback:

Overlay-only correction
8.2 EDID Access Failure

Causes:

Permission issues
Missing sysfs entries

Fallback:

xrandr-only detection
9. Extensibility
9.1 Add New Panel Profiles

Extend:

PANEL_DB
9.2 Add Techniques
Extend TECHNIQUE_INFO
Add rendering method
Integrate into selection logic
10. Design Philosophy
10.1 Perceptual Accuracy > Raw Adjustment

Corrections are:

Gamma-aware
Physically motivated
Human-vision aligned
10.2 Conservative Defaults

Unknown panels:

Use safe baseline
Avoid overcorrection
10.3 Composability

System layers:

Detection → Profiling → Correction → Rendering

Each stage is independent and replaceable.

11. Security & Safety
No privileged escalation
Read-only EDID access
xrandr commands scoped per display
12. Future Enhancements
Wayland support (gamma-control protocol)
HDR-aware correction
Real-time luminance sampling
ML-based panel classification
13. Summary

LuminaFix transforms display correction from:

heuristic tweaking

into:

modeled, perceptual, physics-aligned correction
```
````
