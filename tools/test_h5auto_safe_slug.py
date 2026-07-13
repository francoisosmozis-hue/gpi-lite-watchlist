from api_main import _h5auto_safe_slug


def test_h5auto_safe_slug_removes_french_accents():
    value = "13_07_2026 - LE MANS - Prix des B\u00e9n\u00e9voles_ Pronostics & Cotes"
    assert _h5auto_safe_slug(value) == "13_07_2026_le_mans_prix_des_benevoles_pronostics_et_cotes"


def test_h5auto_safe_slug_handles_apostrophes_and_special_chars():
    value = "Prix de l'\u00c9toile d'Or - \u00c0 l'arriv\u00e9e !"
    assert _h5auto_safe_slug(value) == "prix_de_l_etoile_d_or_a_l_arrivee"


def test_h5auto_safe_slug_is_ascii_only():
    value = "\u00c9t\u00e9 \u00e0 Vincennes - D\u00e9part num\u00e9ro 1"
    slug = _h5auto_safe_slug(value)
    slug.encode("ascii")
    assert "\u00e9" not in slug
    assert "\u00e0" not in slug
    assert "-" not in slug


def test_h5auto_safe_slug_existing_plain_name():
    value = "Prix des Points PMU du Mans_ Pronostics & Cotes"
    assert _h5auto_safe_slug(value) == "prix_des_points_pmu_du_mans_pronostics_et_cotes"
