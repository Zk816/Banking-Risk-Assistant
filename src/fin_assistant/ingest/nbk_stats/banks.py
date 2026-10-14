import re


def normalise_bank(name: str) -> str:
    s = re.sub(r"\(.*?\)|\*+|\)", " ", name)
    s = re.sub(r"[\"«»“”'`]", " ", s)
    s = re.sub(r"\b(ао|дб|до|ао дб|дочерний банк|акционерное общество)\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+\d{1,2}$", "", s.strip())
    return re.sub(r"\s+", " ", s).strip().lower()


CANONICAL: dict[str, str] = {
    "сбербанк": "Bereke Bank",
    "сбербанк россии": "Bereke Bank",
    "bereke bank": "Bereke Bank",
    "цеснабанк": "Alatau City Bank",
    "first heartland jýsan bank": "Alatau City Bank",
    "first heartland jusan bank": "Alatau City Bank",
    "jysan bank": "Alatau City Bank",
    "alatau city bank": "Alatau City Bank",
    "жилстройсбербанк казахстана": "Otbasy Bank",
    "отбасы банк": "Otbasy Bank",
    "банк тураналем": "BTA Bank",
    "банктураналем": "BTA Bank",
    "бта банк": "BTA Bank",
    "банк фридом финанс казахстан": "Freedom Bank",
    "фридом банк казахстан": "Freedom Bank",
    "home credit bank": "Home Credit Bank",
    "банк хоум кредит": "Home Credit Bank",
    "хоум кредит банк": "Home Credit Bank",
    "хоум кредит энд финанс банк": "Home Credit Bank",
    "банк bank rbk": "Bank RBK",
    "bank rbk": "Bank RBK",
    "банк rbk": "Bank RBK",
    "торгово-промышленный банк китая в алматы": "ICBC Almaty",
    "торгово-промышленный банк китая в г. алматы": "ICBC Almaty",
    "тпб китая в г.алматы": "ICBC Almaty",
    "тпбк": "ICBC Almaty",
    "исламский банк заман-банк": "Zaman Bank",
    "иб заман-банк": "Zaman Bank",
    "заман-банк": "Zaman Bank",
    "кзи банк": "KZI Bank",
    "казахстан-зираат интернешнл банк": "KZI Bank",
    "казахстан – зираат интернешнл банк": "KZI Bank",
    "банк бта банк - темiрбанк": "Temirbank",
    "бта банк - темiрбанк": "Temirbank",
    "банк туран алем - темiрбанк": "Temirbank",
    "банктураналем - темiрбанк": "Temirbank",
    "темiрбанк": "Temirbank",
    "сеним-банк": "Senim Bank",
    "сенім-банк": "Senim Bank",
    "банк позитив": "Bank Pozitiv",
    "банк позитив казахстан": "Bank Pozitiv",
    "народный банк казахстана": "Halyk Bank",
    "банк центркредит": "Bank CenterCredit",
    "fortebank": "ForteBank",
    "евразийский банк": "Eurasian Bank",
    "нурбанк": "Nurbank",
    "altyn bank": "Altyn Bank",
    "kmf банк": "KMF Bank",
    "kaspi bank": "Kaspi Bank",
    "банк втб": "VTB Kazakhstan",
    "ситибанк казахстан": "Citibank Kazakhstan",
    "шинхан банк казахстан": "Shinhan Bank Kazakhstan",
    "банк китая в казахстане": "Bank of China Kazakhstan",
}

KASE_CODE: dict[str, str] = {
    "Halyk Bank": "HSBK",
    "Bank CenterCredit": "CCBN",
    "ForteBank": "ASBN",
    "Freedom Bank": "FFBN",
    "Eurasian Bank": "EUBN",
    "Home Credit Bank": "HCBN",
    "Bereke Bank": "BERK",
    "Bank RBK": "INBN",
    "Nurbank": "NRBN",
    "Altyn Bank": "ATBN",
    "Alatau City Bank": "TSBN",
    "Otbasy Bank": "JSBN",
    "KMF Bank": "MFKM",
}


def canonical_bank(name: str) -> str:
    norm = normalise_bank(name)
    return CANONICAL.get(norm, norm)
