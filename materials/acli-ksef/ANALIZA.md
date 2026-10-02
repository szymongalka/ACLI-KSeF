# Co wykorzystać z acli-ksef / ASEF

Ocena z 2 października 2026 na podstawie statycznego przeglądu wybranego [snapshotu](README.md). Celem jest wybranie rozwiązań pomocnych w **ACLT-KSeF**, którego agent ma szybko obsługiwać cały zakres przez CLI w Pythonie. Nie przyjmujemy całej architektury źródłowej jako gotowej implementacji.

## Przydatne rozwiązania i etapy

| Obszar | Gdzie szukać | Zastosowanie w ACLT-KSeF |
| --- | --- | --- |
| Wspólny rdzeń dla CLI i agenta | [service.py](snapshot/src/asef/service.py), [cli.py](snapshot/src/asef/cli.py) | M1–M3: jeden zestaw funkcji biznesowych. Nowy kontrakt JSON i komendy zadaniowe wynikają z naszej specyfikacji. |
| Prywatne pliki i atomowy zapis | [paths.py](snapshot/src/asef/paths.py) | M1–M2: zapis przez plik tymczasowy, utrwalenie i podmianę; dopasowanie katalogów do profilu oraz Linuxa. |
| Eksport paczek | [ksef_export.py](snapshot/src/asef/ksef_export.py) | M2: szyfrowanie, weryfikacja części i XML, ograniczenia archiwum oraz import bez niebezpiecznego rozpakowania. Kontynuację i wznowienie projektujemy zgodnie z naszym kontraktem. |
| Kwoty i lokalne XSD | [invoice.py](snapshot/src/asef/invoice.py), [schematy](snapshot/src/asef/xsd/NOTICE.md) | M2–M3: `Decimal`, zachowanie precyzji ceny jednostkowej, lokalne importy XSD i parser bez pobierania encji. Reguły finansowe potwierdzamy na przypadkach operatora. |
| Kontrola szkicu | [check_draft.py](snapshot/scripts/check_draft.py) | M3: kontrola danych, sum i wygenerowanego XML przed podglądem. Włączamy ją do `drafts prepare`, bez dodatkowego ręcznego skryptu dla agenta. |
| Zatwierdzony XML i odporna wysyłka | [service.py](snapshot/src/asef/service.py), [db.py](snapshot/src/asef/db.py) | M3: zgoda na dokładne bajty XML, atomowe zajęcie operacji, zapis referencji i odzyskiwanie niepewnego wyniku. Dodajemy trwałe wersje dokumentu i pełny kontekst zgody. |
| Podgląd z zapisanego XML | [preview.py](snapshot/src/asef/preview.py), [szablon HTML](snapshot/src/asef/templates/invoice.html.j2), [pdf_layout.py](snapshot/src/asef/pdf_layout.py) | M2, M4–M5: wierny odczyt danych, rozróżnienie należności i płatności, porównanie korekty. Branding ACLT-KSeF i fonty na Linuxie wymagają osobnego wykonania i oględzin. |
| Uwierzytelnianie certyfikatem | [xades_auth.py](snapshot/src/asef/xades_auth.py), [certificates.py](snapshot/src/asef/certificates.py) | Ocena w M1, wykonanie w M5: kandydat stosu XAdES; zgodność trzeba potwierdzić na docelowych środowiskach i poświadczeniach. |
| Archiwa i odtworzenie | [db_archive.py](snapshot/src/asef/db_archive.py), [secure_archive.py](snapshot/src/asef/secure_archive.py), [bulk_xml.py](snapshot/src/asef/bulk_xml.py) | M5: inspiracja dla manifestów, kontroli integralności i odtworzenia. Zakres kopii oraz blokada ponownej wysyłki muszą odpowiadać naszej specyfikacji. |

## Elementy wymagające zmiany

**Sposób pracy agenta.** Historyczny [skill](snapshot/SKILL.reference.md) zawiera ścieżki konkretnego serwera, wiele osobnych kroków i wskazówki ręcznego filtrowania danych. ACLT-KSeF potrzebuje krótkiej mapy zadań, `describe`, stabilnego JSON z `schema_version` i `next_action`, zebranych błędów wejścia oraz komend `prepare`, `send` i `status`. Lokalne wyszukiwanie ma mieć filtry i stronicowanie w CLI. Agent nie powinien pisać SQL ani samemu kopiować poprzednich wersji XML.

**Wersje i model danych.** Źródłowa rewizja aktualizuje rekord dokumentu. W ACLT-KSeF wersje wejścia, XML i podglądu pozostają niezmienne, a zgoda obejmuje także profil, środowisko, NIP i numer wersji. Powielone tabele podsumowań oraz pojedynczy kierunek faktury nie stanowią docelowego modelu; jedna faktura może mieć kilka ról podmiotu.

**Generowanie XML.** Generator źródłowy wstawia bieżący czas, więc powtórne generowanie może zmienić skrót dla tego samego wejścia. Generujemy raz, zapisujemy wersję i używamy jej bajtów do podglądu, zgody i wysyłki. Obsługa importowanej korekty w podglądzie nie potwierdza obecności generatora korekt; ten zakres wymaga M4.

**Synchronizacja.** Historyczny kod obsługuje obcięcie eksportu przez dzielenie okien czasu i przechowuje część stanu eksportu w pamięci. W ACLT-KSeF kontynuacja pełnej i obciętej paczki oraz materiał do wznowienia są zapisane trwale. Szczegółowy algorytm określa [nasza specyfikacja](../../SPECYFIKACJA.md); przy implementacji sprawdzamy go ponownie z [przewodnikiem MF](https://github.com/CIRFMF/ksef-api/blob/main/pobieranie-faktur/przyrostowe-pobieranie-faktur.md).

**Czas uruchomienia.** Historyczne CLI ładuje biblioteki dokumentów i kryptografii także w prostych ścieżkach. Nowe lokalne listy i discovery mają uruchamiać się szybko, przez importy tylko tam, gdzie są potrzebne. Długie oczekiwanie na KSeF zwraca trwałą operację zamiast blokować agenta przez kilka minut.

**Integracja OpenClaw.** [Adapter źródłowy](snapshot/openclaw/README.md) wykorzystuje dodatkowy proces Node do Secret Store OpenClaw. To rozwiązanie zależne od wybranej konfiguracji. Przy pierwszym wdrożeniu korzystamy z chronionych plików poświadczeń zgodnie ze specyfikacją. Adapter oceniamy dopiero, jeśli pojawi się wymaganie tego magazynu; nie dodajemy go automatycznie do paczki Pythona.

Historyczne operacje SMB, ścieżki Gateway ONYX, narzędzia publikacji kolekcji skilli, rejestry kontrahentów i branding ASEF pozostają materiałem dodatkowym. Nie wynikają z uzgodnionego zakresu ACLT-KSeF.

## Scenariusze testowe warte przeniesienia

Przenosimy intencję testu i dostosowujemy go do nowego kontraktu. Poniższe testy nie były uruchamiane podczas importu.

| Plik źródłowy | Ryzyko do sprawdzenia | Etap |
| --- | --- | --- |
| [test_money_precision.py](snapshot/tests/test_money_precision.py) | Precyzyjna cena jednostkowa, zaokrąglenie sumy pozycji, nadmierna precyzja i wartości niefinitywne | M3 |
| [test_reliability.py](snapshot/tests/test_reliability.py) | Dwa połączenia wysyłają tę samą wersję; zmieniono zatwierdzone bajty; utracono odpowiedź POST; równoległa zmiana stanu; dokument i historia muszą zapisać się razem | M3 |
| [test_reliability.py](snapshot/tests/test_reliability.py) | Przerwany import strony nie przesuwa kontynuacji; kwoty XML mają pierwszeństwo przed rozbieżnymi metadanymi | M2 |
| [test_ksef_export.py](snapshot/tests/test_ksef_export.py) | Eksport wieloczęściowy, uszkodzenie lub brak części, niezgodne skróty i liczba dokumentów, niebezpieczne elementy ZIP | M2 |
| [test_installment_preview.py](snapshot/tests/test_installment_preview.py) | Podgląd poprawnie odróżnia płatność częściową, raty i należność ogółem | M2, M5 |
| [test_private_files.py](snapshot/tests/test_private_files.py), [test_secure_archive.py](snapshot/tests/test_secure_archive.py), [test_portability.py](snapshot/tests/test_portability.py) | Uprawnienia plików, błędne hasło, naruszenie archiwum i odtworzenie danych | M1, M5 |
| [test_xades_auth.py](snapshot/tests/test_xades_auth.py), [test_certificate_auth.py](snapshot/tests/test_certificate_auth.py) | Konstrukcja podpisu i błędy uwierzytelniania; osobno próba z usługą i rzeczywistym certyfikatem | M1, M5 |

Test dzielenia obciętych okien ze źródła nie zastępuje testu wymaganej kontynuacji. Nowy zestaw musi sprawdzić osobno pełną i obciętą paczkę, wspólną granicę, wiele ról oraz wznowienie eksportu po restarcie.

Do planu trafia wybór rozwiązania wraz z jego ograniczeniami. Dopiero implementacja, właściwe testy i odbiór na docelowym Linuxie zamykają etap; obecność źródeł i historycznych deklaracji testów nie jest takim dowodem.
