"""Body measurement plausibility bounds shared by provider imports."""

BODY_CLAMPS = {"weight_kg": (20, 400), "waist_cm": (30, 250), "chest_cm": (40, 250),
               "neck_cm": (15, 80), "hip_cm": (40, 250), "body_fat_pct": (1, 75)}

# Multi-frequency BIA "body composition analysis report" (§body-composition).
# Wide "physically possible" safety bounds (catch an OCR/typo order-of-
# magnitude error), not clinical-normal ranges — the device's own
# reference bands are stored per-row, not asserted here.
BODY_COMPOSITION_CLAMPS = {
    "height_cm": (50, 250), "age_years": (0, 120),
    "bmi": (10, 80), "body_fat_pct": (1, 75),
    "body_score": (0, 150),   # device note: muscular people may exceed 100
    "target_weight_kg": (20, 400),
    "weight_control_kg": (-200, 200), "fat_control_kg": (-200, 200),
    "muscle_control_kg": (-200, 200),
    "obesity_degree_pct": (0, 500), "visceral_fat_grade": (1, 60),
    "bmr_kcal": (300, 6000), "fat_free_mass_kg": (10, 300),
    "subcutaneous_fat_pct": (1, 75), "smi_kg_m2": (1, 20),
    "body_age": (1, 120), "whr": (0.3, 2.0),
    "component_value_kg": (0, 400), "component_ref_kg": (0, 400),
    "component_pct_of_weight": (0, 200),
    "segment_mass_kg": (0, 100), "segment_pct_of_standard": (0, 2000),
    "segment_std_pct": (0, 500),
    "impedance_khz": (1, 1000), "impedance_ohms": (1, 3000),
}
