"""
<summary>
Space LCD: draws live system stats on the 2.1 inch round screen in the pump head of
an ID-COOLING SPACE LCD cooler, which is a Turing smart screen (hardware revision C).
</summary>
<remarks>
The screen protocol itself comes from turing-smart-screen-python, installed beside this
package by install.sh at a pinned commit. Everything here that does not talk to the screen
(sensors, drawing, config) runs without it, so the tests and the preview mode never touch
the hardware.
</remarks>
"""

__version__ = "dev"
