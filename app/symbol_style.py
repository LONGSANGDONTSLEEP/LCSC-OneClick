"""Appearance presets shared by SchLib export and the SVG preview.

Only presentation changes. Coordinates, pin identities and visibility belong
to the source component and must never be inferred from a style preset.
"""
from dataclasses import dataclass
import re


SYMBOL_STYLES = (('嘉立创风格', 'jlc'), ('AD 风格', 'ad'))


def normalize_symbol_style(value):
    return value if value in ('jlc', 'ad') else 'jlc'


@dataclass(frozen=True)
class SymbolStyle:
    # Classic AD colors; use a compact 8pt font for the existing pin spacing.
    outline: str = '#800000'
    pin: str = '#000000'
    text: str = '#000080'
    area: str = '#ffffb0'
    font: str = 'Times New Roman'
    font_size: int = 8
    line_width: int = 1  # AD Small = 10 mil = one EasyEDA schematic unit.
    light_fill_threshold: int = 192

    def fill(self, value):
        """Keep transparent shapes and solid polarity/arrow marks distinct."""
        if str(value).strip().casefold() == 'none':
            return value
        value = value or '#000000'
        if re.fullmatch(r'#[0-9a-fA-F]{3}', value):
            value = '#' + ''.join(c * 2 for c in value[1:])
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
            return value  # The exporter still reports unsupported source data.
        return self.area if sum(bytes.fromhex(value[1:])) / 3 >= self.light_fill_threshold else self.outline

    def preview(self):
        # One source unit is 10 mil; one point is 1/72 inch. SVG font sizes
        # are expressed in source units, independent of browser pixel density.
        return dict(outline=self.outline, pin=self.pin, text=self.text, area=self.area,
                    font=self.font, fontSize=self.font_size / 0.72,
                    lineWidth=self.line_width, lightFillThreshold=self.light_fill_threshold)


AD_STYLE = SymbolStyle()


def symbol_style(value):
    return AD_STYLE if normalize_symbol_style(value) == 'ad' else None
