"""
drug_data.py - a SMALL demo knowledge base used by the safety rules.

IMPORTANT: this is a teaching/demo list, NOT a complete clinical database.
A real product must use a licensed drug database (e.g. RxNorm / local
formulary + interaction database) and go through clinical governance.
"""
import re
from typing import Optional, Tuple

# How many times per day each frequency code means.
FREQ_PER_DAY = {"OD": 1, "BD": 2, "TDS": 3, "QID": 4, "HS": 1, "PRN": 0}
FREQ_LABEL = {
    "OD": "once daily", "BD": "twice daily", "TDS": "three times daily",
    "QID": "four times daily", "HS": "at bedtime", "PRN": "only when needed",
}

# Brand names common in Pakistan -> generic name.
BRAND_TO_GENERIC = {
    "panadol": "paracetamol", "calpol": "paracetamol", "brufen": "ibuprofen",
    "augmentin": "amoxicillin", "amoxil": "amoxicillin", "risek": "omeprazole",
    "glucophage": "metformin", "disprin": "aspirin", "flagyl": "metronidazole",
    "voltral": "diclofenac", "ponstan": "mefenamic acid", "zithromax": "azithromycin",
    "ciproxin": "ciprofloxacin", "lipitor": "atorvastatin", "norvasc": "amlodipine",
}

# Therapeutic classes (used for duplicate-therapy and class-allergy checks).
DRUG_CLASSES = {
    "penicillin": {"amoxicillin", "ampicillin", "penicillin v", "flucloxacillin", "piperacillin"},
    "nsaid": {"ibuprofen", "diclofenac", "naproxen", "mefenamic acid", "ketorolac", "aspirin"},
    "statin": {"atorvastatin", "simvastatin", "rosuvastatin"},
    "ppi": {"omeprazole", "esomeprazole", "pantoprazole"},
    "ace_inhibitor": {"enalapril", "lisinopril", "ramipril"},
    "sulfonylurea": {"gliclazide", "glimepiride", "glibenclamide"},
    "macrolide": {"azithromycin", "clarithromycin", "erythromycin"},
    "fluoroquinolone": {"ciprofloxacin", "levofloxacin", "ofloxacin"},
    "nitrate": {"isosorbide mononitrate", "isosorbide dinitrate", "nitroglycerin"},
}

# Highest commonly-used TOTAL daily dose in mg (adult). Demo values only.
MAX_DAILY_MG = {
    "paracetamol": 4000, "ibuprofen": 2400, "metformin": 3000,
    "amoxicillin": 3000, "diclofenac": 150, "naproxen": 1500, "mefenamic acid": 1500,
}

# (drug-or-class A, drug-or-class B, severity, message)
INTERACTIONS = [
    ("warfarin", "nsaid", "high", "Warfarin + NSAID: higher risk of bleeding."),
    ("clarithromycin", "statin", "high", "Clarithromycin + statin: higher risk of muscle damage (myopathy)."),
    ("sildenafil", "nitrate", "critical", "Sildenafil + nitrate: risk of severe low blood pressure."),
    ("ace_inhibitor", "spironolactone", "moderate", "ACE inhibitor + spironolactone: risk of high potassium."),
    ("ace_inhibitor", "nsaid", "moderate", "ACE inhibitor + NSAID: may reduce kidney function and blood-pressure control."),
    ("aspirin", "ibuprofen", "moderate", "Aspirin + ibuprofen: ibuprofen may weaken aspirin's heart-protective effect."),
    ("metronidazole", "warfarin", "high", "Metronidazole + warfarin: warfarin effect can increase markedly."),
]

# Drugs / classes to be careful with when kidney tests are abnormal.
RENAL_CAUTION = {"nsaid", "metformin"}


def normalize_medicine(name: str) -> str:
    """'Panadol 500' -> 'paracetamol'; 'Metformin HCl' -> 'metformin'."""
    n = name.lower().strip()
    n = re.sub(r"\b(tablets?|tabs?|capsules?|caps?|syrup|injection|hcl|sr|xr|er|forte)\b", "", n)
    n = re.sub(r"[\d.]+\s*(mg|g|mcg|ml|iu)?", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return BRAND_TO_GENERIC.get(n, n)


def classes_of(generic: str) -> set:
    return {cls for cls, members in DRUG_CLASSES.items() if generic in members}


def parse_strength(text: str) -> Tuple[Optional[float], str]:
    """'500 mg' -> (500.0, 'mg'); '1 g' -> (1000.0, 'mg'); '250 mg/5 ml' -> (250.0, 'mg')."""
    m = re.search(r"([\d.]+)\s*(mg|g|mcg|µg|ml|iu)", text.lower())
    if not m:
        return None, ""
    value, unit = float(m.group(1)), m.group(2)
    if unit == "g":
        return value * 1000, "mg"
    if unit in ("mcg", "µg"):
        return value / 1000, "mg"
    return value, unit
