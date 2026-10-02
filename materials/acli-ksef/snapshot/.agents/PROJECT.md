# ASEF — informacja dla agentów

ASEF oznacza **Agencyjny System Elektronicznych Faktur**. To lokalna aplikacja Python dla jednej osoby, obsługiwana przede wszystkim przez agenta AI.

Autor podglądu: **Szymon Gałka**, `kontakt@szymongalka.dev`, `szymongalka.dev`. Podpis autora jest elementem prezentacji, oddzielonym od danych sprzedawcy w XML faktury.

## Architektura

- `src/asef/` zawiera jedyny rdzeń biznesowy i klienta API KSeF.
- CLI wywołuje rdzeń biznesowy; `.agents/skills/asef/SKILL.md` opisuje użycie CLI przez agenta.
- `docs/openclaw.md` opisuje użycie skilla i CLI bezpośrednio na hoście OpenClaw Gateway.
- `.agents/BRAND.md` opisuje znak ASEF i motywy jasny oraz CRT.
- Dane użytkownika są poza repozytorium (`ASEF_DATA_DIR` albo katalog aplikacji systemu). Nie zapisuj tokenów, kluczy, faktur ani bazy w Git.
- SQLite utrzymuje oryginalny XML oraz czytelne tabele `wystawione_faktury`, `odebrane_faktury`, `korekty`.

## Ważne reguły

- FA(3) XML jest źródłem prawdy dla faktury. PDF i HTML są wizualizacjami tego samego XML.
- Zmiana XML po zatwierdzeniu unieważnia zatwierdzenie. Wysyłaj tylko dokładnie zatwierdzone bajty XML.
- Domyślnym środowiskiem jest TEST. PROD wymaga jawnego wyboru i oddzielnego potwierdzenia.
- Nie wypisuj sekretów w CLI, logach ani testach.
- Dokumenty pobieraj do lokalnej bazy; wyszukiwanie i podgląd korzystają z danych lokalnych.
- Reguły stawek VAT muszą mieć źródło i datę obowiązywania. Nie przypisuj stawki automatycznie z nazwy towaru/usługi.

## Źródła kontraktu

- KSeF API: https://github.com/CIRFMF/ksef-api
- FA(3): https://ksef.podatki.gov.pl/informacje-ogolne-ksef-20/faktura-ustrukturyzowana-i-struktura-logiczna-fa/
- Synchronizacja: https://github.com/CIRFMF/ksef-api/blob/main/pobieranie-faktur/przyrostowe-pobieranie-faktur.md
