> Snapshot inspiracyjny acli-ksef/ASEF dla ACLT-KSeF. Poniższy README opisuje projekt źródłowy i jego historyczne środowisko. Zobacz [opis importu](../README.md) oraz [analizę](../ANALIZA.md).

# ASEF

**Gateway ONYX — CLI:** używaj `asef --json ...` (`/usr/local/bin/asef`).
`auth check`, `sync`, `invoice status` i zatwierdzona `invoice send` korzystają
z istniejącego OpenClaw Secret Store; bez zależności od narzędzia `asef_ksef`.
[Komendy i zasady wysyłki](docs/openclaw.md). Sam entry point Pythona wymaga
`--credentials openclaw`; tryb `system` pozostaje dostępny dla keyring/systemd.

**Agencyjny System Elektronicznych Faktur** to lokalne narzędzie Python dla jednej osoby i jej agenta AI. Agent korzysta z CLI według przypiętego wydania [ACLI-KSeF](SKILL.reference.md). Kod tego wydania jest w `/root/.openclaw/acli-skills/acli-ksef/`, a aktywna baza i pliki wynikowe w `/root/.openclaw/acli-database/acli-ksef/`.

ASEF zapisuje oryginalne faktury FA(3) XML w SQLite oraz ich czytelne dane w tabelach `wystawione_faktury`, `odebrane_faktury` i `korekty`. HTML i PDF powstają z tego samego XML, który może zostać wysłany do KSeF.

**Stan projektu:** wersja 0.1.0. Lokalny obieg szkicu, podglądu, zatwierdzenia i synchronizacji ma testy automatyczne. Uwierzytelnianie certyfikatem sprawdzono także na żywym KSeF TEST z losowym fikcyjnym NIP i samopodpisanym certyfikatem EC: usługa zaakceptowała żądanie XAdES i wydała tokeny dostępu oraz odświeżania. Nie testowano rzeczywistego certyfikatu KSeF ani tej metody na DEMO lub PROD. Pełny obieg wysyłki i odzyskiwania po awarii na KSeF DEMO oraz wysyłka na PROD nie zostały jeszcze potwierdzone na żywej usłudze. Przykład i zrzuty ekranu zawierają wyłącznie dane demonstracyjne.

## Start

Wymagany jest Python 3.11+ oraz [uv](https://docs.astral.sh/uv/). W katalogu projektu:

```sh
uv sync --extra dev
uv run asef --help
uv run asef profile add 1111111111 "Firma demonstracyjna" --env test
uv run asef invoice create --file examples/faktura.json
```

Przykład ma fikcyjne dane i służy wyłącznie do podglądu. Wynik `invoice create` zawiera `id` dokumentu. Podstaw tę wartość w kolejnych komendach:

```sh
uv run asef invoice preview ID --format html --out podglad.html
uv run asef invoice preview ID --format pdf --theme light --out podglad-jasny.pdf
uv run asef invoice preview ID --format pdf --theme crt --out podglad-crt.pdf
uv run asef invoice show ID
uv run asef invoice list --category issued
```

[Przykładowy HTML](docs/preview.html) pokazuje wygląd faktury; PDF generuj lokalnie poleceniami powyżej i zapisuj w katalogu danych, nie w kodzie skilla. Tabela pozycji zawiera numer, szeroki opis, ilość, jednostkę, cenę netto, stawkę VAT, kwotę VAT i cenę brutto. Zestawienie VAT jest widoczne, gdy XML zawiera odpowiednie kwoty zbiorcze. Pole P_15 jest prezentowane jako kwota należności ogółem (dla korekty: korekta kwoty należności); odrębna kwota do zapłaty lub rozliczenia pojawia się tylko wtedy, gdy XML zawiera ją w sekcji Rozliczenie. Dane korekty, miejsca wystawienia, zapłaty i rachunku bankowego pojawiają się tylko wtedy, gdy występują w dokumencie. Dla przyjętych faktur z numerem KSeF podgląd zawiera lokalnie wygenerowany kod QR do oficjalnego serwisu weryfikacji. HTML zachowuje wąski, mobilny układ także na komputerze; tabelę pozycji przewija się poziomo. PDF ma jeden wspólny format 500 × 1500 pt dobrany do czytania na iPhonie 17 Pro Max i iPadzie mini. Przy większej liczbie pozycji powstają kolejne strony. Układ PDF jest nastawiony na ekran; nie jest standardowym formatem A4 do druku.

W JSON szkicu można podać `issue_place`, `sale_date`, `payment_due_date`, `payment_method` (np. `przelew`, `gotówka`, `karta`) i `bank_account` z polami `number`, `swift`, `bank`, `description`. ASEF zapisuje je w polach FA(3). Podgląd odczytuje również pola z zaimportowanego XML, w tym korekty oraz informację o płatności. Brakujących sekcji nie wyświetla.

Branding ASEF ma własny znak A i dwa warianty kolorystyczne. [Wektory logo](assets/asef-logo-light.svg) i [motywu CRT](assets/asef-logo-crt.svg) są dostępne do dalszego użycia. Ciemny PDF nawiązuje do pokazanej referencji CRT przez czarne tło, białe i szare elementy, monospacowe etykiety i subtelne linie ekranu. Żółć pozostaje drobnym akcentem w znaku.

Autorem podglądu ASEF jest **Szymon Gałka** — [szymongalka.dev](https://szymongalka.dev), [kontakt@szymongalka.dev](mailto:kontakt@szymongalka.dev). Podpis znajduje się w stopce wizualizacji i jest oddzielony od danych stron faktury.

## Obieg KSeF

1. W TEST używaj wyłącznie fikcyjnych danych i losowego NIP; nie przesyłaj rzeczywistych danych firm ani faktur do TEST lub DEMO. Profil dodaj przez `asef profile add NIP "Nazwa" --env test|demo|prod`. Bez `--env` nowy profil trafia do TEST; jeśli NIP ma już profil poza TEST, podaj środowisko jawnie. Jeden NIP może mieć oddzielne profile dla różnych środowisk.
2. Wybierz poświadczenie profilu. Token zapisz w interaktywnym terminalu przez `uv run asef auth set-token --nip NIP --env test`; trafi do systemowego magazynu haseł. Aby używać certyfikatu KSeF, zarejestruj parę `.crt` i zaszyfrowanego `.key` według instrukcji niżej, a następnie wybierz ją przez `uv run asef auth use-certificate ODCISK --nip NIP --env test`. Po wyborze sprawdź połączenie przez `uv run asef auth check --nip NIP --env test`. Typ certyfikatu `Offline` nie służy do logowania.
3. Utwórz szkic z JSON albo zaimportuj istniejący FA(3) XML: `uv run asef invoice import-xml faktura.xml --nip NIP`. Generator JSON obsługuje obecnie zwykłe faktury PLN ze stawkami 23%, 8% i 5%. Cena jednostkowa netto może mieć do 8 miejsc po przecinku; kwota pozycji jest zaokrąglana po przemnożeniu przez ilość. Inne przypadki, w tym korekty, wymagają gotowego XML FA(3).
4. Obejrzyj HTML lub PDF. Podgląd zwraca SHA-256 XML. Użytkownik może zatwierdzić tę wersję w interaktywnym terminalu: `uv run asef invoice approve ID --sha256 HASH`. Po jego wyraźnej zgodzie agent może zatwierdzić ją bez pytania CLI przez `uv run asef invoice approve ID --sha256 HASH --yes`. Hash musi odpowiadać aktualnemu XML; zmiana szkicu unieważnia zatwierdzenie.
5. Po zatwierdzeniu wyślij: `uv run asef invoice send ID`. Dla PROD wymagane jest osobne potwierdzenie w terminalu albo `--confirm-prod HASH` po wyraźnej zgodzie na tę wysyłkę. Następnie sprawdź wynik asynchroniczny: `uv run asef invoice status ID`. UPO wyeksportujesz przez `uv run asef invoice upo ID --out upo.xml`.

Jeśli wynik POST faktury jest niepewny, dokument pozostaje w stanie `sending` i nie można go wysłać drugi raz automatycznie. `invoice status` może odnaleźć fakturę w zapisanej sesji po skrócie XML, ponowić nieudane zamknięcie sesji i pobrać UPO. Stan `preparing` rezerwuje dokument przed połączeniem z KSeF; jeśli proces przerwał się przed zapisaniem sesji i przed POST, `invoice status` przywraca zatwierdzenie. Błąd przed POST oraz jednoznaczna odmowa HTTP 400 pozostawiają dokument zatwierdzony; po sprawdzeniu przyczyny można świadomie ponowić wysyłkę w nowej sesji.

## Synchronizacja i źródła zewnętrzne

Pierwsza synchronizacja wymaga początku zakresu UTC, na przykład:

```sh
uv run asef sync --nip NIP --from 2026-02-01T00:00:00Z
uv run asef registry check NIP
uv run asef vat rates
uv run asef db path
uv run asef db backup kopia.sqlite3
uv run asef db export asef-export.asef
uv run asef invoice export-bundle faktury.asefxml
```

Synchronizacja odpytuje role Podmiot 1, Podmiot 2, Podmiot 3 i podmiot upoważniony, dzieli dłuższy okres i obcięte wyniki na mniejsze okna oraz zapisuje punkt wznowienia osobno dla każdej roli. Pobiera faktury typu FA(3); inne struktury są liczone jako `unsupported_skipped`. Do tabel wystawionych i odebranych trafiają wyłącznie dokumenty, w których NIP profilu jest odpowiednio sprzedawcą lub nabywcą; pozostałe dokumenty dostępne jako podmiot trzeci są liczone jako `related_skipped`. Kwoty w bazie pochodzą z XML; rozbieżności względem metadanych KSeF są liczone jako `metadata_mismatches` i zapisywane w audycie. Żądania odczytu respektują krótkie `Retry-After` po HTTP 429; przy dłuższym czasie oczekiwania kończą się błędem bez przesuwania kursora. Dla NIP z kilkoma profilami podaj `--env`. Po dodaniu nowych ról pierwsze `sync` wymaga `--from`, aby określić ich początek.

`registry check` działa bez kluczy: pobiera nazwę, REGON, status VAT i ewentualny numer KRS z publicznego wykazu VAT Ministerstwa Finansów, a po numerze KRS sprawdza otwarte API KRS. Numer KRS można też podać przez `--krs`. Wynik zawiera źródło, datę zapytania i linki do ręcznego sprawdzenia wpisów CEIDG oraz REGON. Nie oznacza danych z wykazu VAT jako weryfikacji wpisu CEIDG lub GUS: oficjalne API tych rejestrów wymagają odpowiednio JWT i klucza użytkownika. Błąd lub brak wpisu w jednym źródle nie oznacza, że firmy nie ma w pozostałych rejestrach.

`vat rates` potwierdza katalog ogólnych stawek na bieżącej stronie Ministerstwa Finansów i zwraca adres źródła oraz czas sprawdzenia. Nie wybiera stawki dla konkretnego towaru ani usługi. Taką decyzję trzeba oprzeć na właściwych przepisach lub WIS.

## Agent

Instrukcja pracy agenta jest w [SKILL.md](SKILL.reference.md). Agent wywołuje CLI, a maszynowy wynik jest dostępny przez `asef --json ...`. [AGENTS.md](AGENTS.md) zawiera ogólne wskazówki repozytorium.

Uruchomienie skilla i CLI bezpośrednio na hoście OpenClaw Gateway opisuje [docs/openclaw.md](docs/openclaw.md).

## Dane i ograniczenia

Baza domyślnie trafia do systemowego katalogu danych aplikacji. Można ją przenieść przez `ASEF_DATA_DIR`. Certyfikat KSeF zarejestruj jawnie jako parę plików `.crt` i zaszyfrowanego `.key`:

```sh
uv run asef auth add-certificate /ścieżka/certyfikat.crt /ścieżka/klucz.key --purpose Authentication
uv run asef auth certificates
uv run asef auth use-certificate ODCISK --nip NIP --env test
uv run asef auth check --nip NIP --env test
```

`--purpose` przyjmuje `Authentication` albo `Offline`. Według [dokumentacji KSeF](https://github.com/CIRFMF/ksef-api/blob/main/certyfikaty-KSeF.md) typ `Offline` służy fakturom offline i **nie umożliwia uwierzytelnienia**. Klucz musi być zaszyfrowanym PEM PKCS#8; jeśli otrzymano go w innym formacie, przygotuj taką postać przed rejestracją. ASEF sprawdza parę certyfikat–klucz i zapisuje ją w prywatnym katalogu `ASEF_DATA_DIR/certificates`. `auth certificates` zwraca odcisk i metadane bez zawartości klucza. ASEF nie wyszukuje certyfikatów automatycznie na dysku.

Rejestracja pyta o hasło klucza w ukrytym monicie tylko po to, aby sprawdzić parę; nie zapisuje go. `auth use-certificate` przypisuje certyfikat `Authentication` do wskazanego profilu i ponownie pyta o hasło w ukrytym monicie, aby zapisać je w systemowym magazynie haseł na potrzeby późniejszego logowania. Opcja `--subject-identifier` przyjmuje `certificateSubject` albo `certificateFingerprint` i pozwala wskazać identyfikator podpisu wymagany przez dany kontekst KSeF. Przy wielu certyfikatach wybierz odcisk jawnie, zamiast zakładać, że ASEF użyje dowolnego z nich. Aby wrócić do tokenu, użyj `uv run asef auth use-token --nip NIP --env test`, a potem `auth check`. Dostępne metody mogą zależeć od uprawnień podmiotu w KSeF. Hasła nie wpisuj w argumentach poleceń ani w rozmowie z agentem.

`asef db export PLIK` tworzy zaszyfrowane hasłem archiwum pełnej, spójnej bazy SQLite: profili, XML faktur, UPO, statusów, szablonów, kursorów i audytu. Dołącza dostępne tokeny KSeF każdego profilu z systemowego magazynu haseł albo poświadczenia systemd oraz wszystkie jawnie zarejestrowane pary certyfikat–klucz. Wynik podaje liczbę certyfikatów i profile bez tokenu. Hasło eksportu musi mieć co najmniej 12 znaków. CLI pyta o nie w ukrytym monicie z powtórzeniem; nie podawaj go w argumentach, czacie ani logach. `asef db backup PLIK` nadal zapisuje prywatną, **niezaszyfrowaną** kopię SQLite bez tokenów i certyfikatów. Eksport nie nadpisuje istniejącego pliku.

Na nowej instalacji z działającym systemowym magazynem haseł ustaw pusty katalog danych i wykonaj `ASEF_DATA_DIR=/ścieżka/do/nowych-danych uv run asef db import asef-export.asef`. CLI zapyta o hasło w ukrytym monicie. Import sprawdza integralność SQLite i skróty XML, odtwarza dostępne tokeny KSeF w magazynie haseł, a zarejestrowane certyfikaty i oryginalne, nadal zaszyfrowane pliki `.key` w prywatnym katalogu `certificates`. Własne hasło klucza `.key` ani wybór certyfikatu do logowania nie są przenoszone przez archiwum. Po imporcie ponownie wykonaj `auth use-certificate ODCISK --nip NIP --env ŚRODOWISKO` albo, jeśli token dla profilu jest dostępny, `auth use-token --nip NIP --env ŚRODOWISKO`, a następnie `auth check`. Import odmówi działania, jeśli docelowa baza, certyfikat o tym samym odcisku albo token dla importowanego profilu już istnieje. Gdy `CREDENTIALS_DIRECTORY` jest ustawione, archiwum zawierające tokeny nie zostanie zaimportowane; poświadczenia systemd trzeba przygotować osobno, przed uruchomieniem ASEF na takim hoście. Po imporcie porównaj liczby profili, dokumentów i certyfikatów oraz otwórz przykładową fakturę, zanim wyłączysz stare źródło danych.

Do przenoszenia samych faktur użyj `asef invoice export-bundle faktury.asefxml` i `asef invoice import-bundle faktury.asefxml`. Eksport można ograniczyć przez `--nip NIP` i `--env test|demo|prod`; oba polecenia pytają o hasło w ukrytym monicie. Pakiet zawiera XML i przypisanie do profili, bez tokenów, certyfikatów i kluczy KSeF, UPO, szablonów i kursorów synchronizacji. Import działa w istniejącej bazie, tworzy brakujące profile, pomija faktury o tym samym XML i nie przenosi zatwierdzenia do wysyłki; dokumenty wymagające wysyłki trzeba ponownie obejrzeć i zatwierdzić. Nie zapisuj archiwów, prawdziwych faktur ani poświadczeń w repozytorium.

Kod integracji KSeF, CEIDG, GUS i KRS oparto na publicznej dokumentacji. Próba z żywym KSeF TEST potwierdziła podpisanie żądania i uzyskanie tokenów przy użyciu fikcyjnego certyfikatu EC; nie potwierdza rzeczywistych uprawnień, pełnego obiegu faktur ani działania z certyfikatem KSeF w DEMO lub PROD. Nie przeprowadzono jeszcze pełnego testu połączenia i odzyskiwania po awarii na DEMO ani wysyłki na PROD. Przed użyciem produkcyjnym sprawdź uprawnienia, poprawność danych, skutki podatkowe, synchronizację, UPO i odtworzenie bazy z kopii na danych nieprodukcyjnych. Generator JSON obejmuje ograniczony podzbiór FA(3); import XML pozwala obsługiwać pozostałe dokumenty bez deklarowania, że ASEF automatycznie wylicza wszystkie ich pola.

Schematy XSD pod `src/asef/xsd/` pochodzą z [oficjalnego repozytorium API KSeF](https://github.com/CIRFMF/ksef-api/tree/main/faktury/schemy/FA) i są używane do lokalnej walidacji. Kontrakt i procedury KSeF należy weryfikować w [dokumentacji API](https://github.com/CIRFMF/ksef-api). Stawki i zakresy VAT pochodzą z [portalu podatki.gov.pl](https://www.podatki.gov.pl/podatki-firmowe/vat/stawki-i-limity).

Testy: `uv run pytest -q`. Budowa pakietu: `uv build`.

Zmiany w repozytorium są sprawdzane przez [GitHub Actions](.github/workflows/ci.yml) na Pythonie 3.11 i 3.13. Zgłoszenia dotyczące bezpieczeństwa oraz zasady udostępniania danych opisuje [SECURITY.md](SECURITY.md). Pochodzenie dołączonych schematów XSD opisuje [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
