# Integracja ACLT-KSeF z ACLT-Bridge

Wersja projektu 0.6. Data 2 października 2026. Autor: **Szymon Gałka**. Kontakt: [kontakt@szymongalka.dev](mailto:kontakt@szymongalka.dev).

Ustalenie użytkownika: poświadczenia do KSeF są przekazywane przez **ACLT-Bridge** z przygotowanych wejść OpenClaw SecretRef. CLI w Pythonie pozostaje częścią skilla ACLT-KSeF. Ten dokument określa plan integracji; adapter KSeF nie jest jeszcze zaimplementowany.

## Źródło oceny

Paczka `aclt-bridge-source-0.2.0.zip` zawiera 15 plików. Przegląd statyczny objął jej listę plików, README, opis konfiguracji i architektury, SECURITY.md, metadane paczki, manifest, `index.mjs` oraz `bridge.mjs`.

SHA-256 archiwum:

```text
1cc4b38f6fc9e96decf000ffa80012f952d280d7cc84b737ba6bc672c30e4774
```

Nie uruchamiano kodu ani testów tej paczki i nie instalowano jej w Gateway. Wersje Node, Pythona i OpenClaw podane w jej README są deklaracją projektu źródłowego. Nie określają minimalnej wersji zgodnej z naszym adapterem.

Paczka deklaruje `UNLICENSED`. Kod i ZIP mostka nie są dołączane do tego repozytorium ani objęte jego GPLv3. Mostek pozostaje osobną zależnością wdrożenia; tutaj zapisujemy własny projekt kontraktu i ocenę dostarczonego materiału.

## Co potwierdza kod 0.2.0

| Element | Stan źródła |
| --- | --- |
| Narzędzie i konfiguracja | `aclt_kim`; manifest wymaga obiektu `kim` i wejść `kim.login`, `kim.password`. |
| Odczyt sekretów | W aktywnym wywołaniu przez `getPreparedPluginSecretInput('aclt-bridge', path).value`. |
| Uruchomienie odbiorcy | `execFile`, bez shella; `/usr/bin/python3 -I`, ścieżka `.py` ustalana przez administratora. |
| Przekazanie wartości | Minimalne środowisko procesu z dwiema zmiennymi KiM; bez dziedziczenia całego środowiska Gateway. |
| Limity | 30 sekund i 512 KiB wyjścia w adapterze KiM. |
| Błędy | Szczegóły subprocess i stderr ukryte; specjalna obsługa dwóch stanów KiM przy kodzie 1. |
| Redakcja | Znane reprezentacje loginu, hasła i Basic Auth. Nie jest to ogólna ochrona każdego rodzaju sekretu. |

Mostek nie rozwiązuje sam dostawców SecretRef i nie ma własnego cache poświadczeń. OpenClaw przygotowuje snapshot w pamięci, a wywołania odczytują aktywny stan. Po rotacji trzeba potwierdzić skuteczny reload; sam kolejny odczyt nie oznacza odświeżenia wartości u dostawcy. [Model runtime OpenClaw](https://docs.openclaw.ai/gateway/secrets/runtime-model), [rotacja i operacje](https://docs.openclaw.ai/gateway/secrets/operations).

## Projekt adaptera KSeF

Planowana nazwa narzędzia to **`aclt_ksef`**. Model przekazuje operację, profil i zamknięty zestaw danych biznesowych. Przykład planowanego kontraktu:

```json
{
  "operation": "invoices.list",
  "profile": "test",
  "filters": {"direction": "received"},
  "refresh": true
}
```

Adapter mapuje taki obiekt na jedną komendę CLI. Nie przyjmuje od modelu dowolnych argumentów powłoki, interpreterów, ścieżek kodu, URL API ani referencji sekretów. Schemat operacji musi odpowiadać parserowi i `describe` CLI. Ustrukturyzowane wejście dokumentu trafia do stdin procesu. Ścieżki artefaktów podlegają regułom katalogów danych profilu.

Konfiguracja administratora wiąże udostępniony profil z paczką skilla, katalogami danych, środowiskiem KSeF i wymaganymi SecretRefs. Profil wybrany przez model jest sprawdzany z tym powiązaniem. Konfiguracja KSeF jest niezależna od KiM; samo dołączenie jej do manifestu z obowiązkowym `kim` nie wystarczy.

| Wymaganie | Projekt wykonania |
| --- | --- |
| Python z zależnościami skilla | Jawna ścieżka `.venv/bin/python`, tryb `-I` i moduł `aclt_ksef` zainstalowany w tym środowisku. Nie używamy systemowego Pythona KiM dla kodu wymagającego zależności. |
| Poświadczenia M1 | Token KSeF z przygotowanego SecretRef, w proponowanej zmiennej procesu `ACLT_KSEF_BOOTSTRAP_TOKEN`; nazwa jest elementem nowego kontraktu, nie funkcją 0.2.0. |
| Środowisko procesu | Zamknięta lista potrzebnych zmiennych, katalogi ustawione przez administratora, bez wartości sekretów w argv. |
| Operacje lokalne | Discovery, lokalne listy, przygotowanie szkicu i podgląd nie pobierają sekretów. |
| Operacje zdalne | Wymagane wejścia są sprawdzane przed uruchomieniem CLI; brak kończy się `CREDENTIALS_UNAVAILABLE` i `next_action: configure`. |
| Wynik i błędy | Koperta JSON z naszej specyfikacji dla sukcesu i kodów 2–9. Brak zwracania surowego stderr, komendy, wyjątku subprocess lub częściowego wyjścia. |
| Redakcja | Dostosowana do tokena i materiału certyfikatu. CLI samo nie zwraca tokenów wydanych przez KSeF; redakcja mostka jest dodatkową warstwą. |
| Ponawianie | Adapter sam nie powtarza wywołania CLI. Narzędzie obejmujące wysyłkę deklaruje `sideEffecting: true`, `replaySafe: false`. |

Limity czasu i wyniku ustalamy dla KSeF. CLI kończy ograniczoną pracę i zwraca `processing` z identyfikatorem przed limitem mostka. Nie kopiujemy 30 sekund KiM jako domyślnego czasu kompletnego eksportu lub wysyłki.

Przed uruchomieniem procesu mostek tworzy `invocation_id` i przekazuje go w kontekście wejścia. CLI wiąże go trwale z operacją przed skutkiem zdalnym. Przy przerwaniu bez kompletnego JSON mostek zwraca bezpieczny `BRIDGE_EXECUTION_INTERRUPTED`, identyfikator wywołania i wskazanie odzyskiwania. `operations recover --invocation-id ID` umożliwia odnalezienie operacji po tym identyfikatorze. Rozpoczęta wysyłka nie może zostać ponowiona dlatego, że wrapper nie dostał wyniku.

## Poświadczenia źródłowe i stan sesji

Token KSeF, klucz prywatny i hasło do niego są poświadczeniami źródłowymi. Dostarcza je ACLT-Bridge; CLI nie zapisuje ich trwałej kopii i nie ma alternatywnego dostępu do dostawcy sekretów.

KSeF wydaje osobne `accessToken` i `refreshToken`. Mostek 0.2.0 nie zapewnia magazynu ani aktualizacji takich wartości. Proponujemy chroniony stan sesji w danych profilu, rozdzielony według środowiska, NIP i odcisku zestawu poświadczeń; decyzja D06 ustala to przed M1. Operacja zdalna nadal wymaga dostępnych wejść z mostka. Zmiana poświadczeń źródłowych unieważnia niezgodną sesję, a standardowa kopia danych nie zawiera sekretów.

W M5 adapter otrzyma materiał wymagany do uwierzytelnienia certyfikatem. Przed tym etapem ustalamy format i limity. Wielowierszowy PEM wymaga walidacji właściwej dla tego formatu; nie dziedziczy reguły KiM odrzucającej CR/LF.

Poświadczenia są obecne w pamięci Gateway i odbiorcy, a w proponowanym M1 także w środowisku procesu. Mostek ogranicza ujawnienie modelowi, ale nie izoluje od uprawnionego procesu lokalnego i nie gwarantuje zerowania pamięci.

## Odbiór i zakres prac

M1 obejmuje uzgodnienie kontraktu, adapter w projekcie ACLT-Bridge, odbiorcę w skillu i testy syntetyczne. Odbiór sprawdza brak sekretów i fallbacku, profile, właściwy interpreter, redakcję, pełny kontrakt błędów, limity i reload. Rzeczywiste logowanie TEST jest osobnym dowodem.

M3 sprawdza timeout i anulowanie podczas wysyłki oraz odzyskanie operacji bez powtórzenia. M5 dodaje certyfikat i odbiór na docelowym Linuxie z aktywnym Gateway. Raport zapisuje wersję załadowanego mostka, CLI, OpenClaw oraz wynik operacji; zaliczone testy offline nie potwierdzają tego wdrożenia.
