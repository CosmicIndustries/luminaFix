# LuminaFix — Display Uniformity Corrector

LuminaFix is a Linux-based display correction tool that automatically detects your monitor hardware via EDID, profiles known panel characteristics, and applies perceptually accurate corrections for backlight bleed, gradient non-uniformity, and clouding.

It combines **hardware-aware modeling**, **gamma correction**, and **real-time overlay compositing** to improve visual uniformity across a wide range of panels.

---

## ✦ Core Capabilities

- 🔍 **Automatic Display Detection**
  - Parses EDID from `/sys/class/drm`
  - Reconciles with `xrandr` outputs
  - Supports multi-monitor setups

- 🧠 **Panel Intelligence Engine**
  - Manufacturer-based heuristics (AUO, BOE, LGD, Samsung, etc.)
  - Pre-profiled uniformity characteristics
  - Conservative fallback for unknown panels

- 🎯 **Adaptive Correction Pipeline**
  - PPI-aware severity normalization
  - Composite + worst-case severity scoring
  - Technique auto-selection:
    - Combined correction
    - Edge masking
    - Gamma-only adjustment
    - Minimal mode

- 🎨 **Overlay Rendering (Cairo + GTK3)**
  - Exponential edge falloff (physically modeled)
  - Radial uniformity correction
  - Per-pixel alpha blending

- ⚙️ **Gamma + Color Temperature Compensation**
  - xrandr LUT manipulation
  - CCT-based RGB gamma offsets (Kang et al. approximation)
  - Per-channel correction

---

## ✦ Correction Techniques

| Technique         | Description |
|------------------|------------|
| **Combined**      | Overlay + gamma; aggressive correction for severe bleed |
| **Edge Mask**     | Visual overlay only; preserves color fidelity |
| **Gamma Darken**  | Subtle LUT adjustment; minimal intrusion |
| **Uniformity Mask** | Radial compensation for brightness gradients |
| **Minimal**       | No correction needed |

---

## ✦ Installation

### Dependencies

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 python3-cairo x11-xserver-utils# luminaFix
fix gradient lighting
