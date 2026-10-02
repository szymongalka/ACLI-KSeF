# ASEF na hoście OpenClaw Gateway

## Zwykłe CLI z Secret Store (od 2026-09-28)

ASEF ma natywny adapter JavaScript/Node.js w [`../openclaw/`](../openclaw/README.md)
oraz prywatny most Python `asef.openclaw_bridge`. Token jest rozwiązywany przez
kontrakt SecretRef OpenClaw i podawany ASEF przez anonimowy potok; bez pliku
poświadczenia, wartości w argv/env i bez ujawniania go agentowi. Nie należy
przekazywać CLI egress-sentinela ani odczytywać bazy magazynu.

Na tym Gateway `/usr/local/bin/asef` uruchamia istniejący Python CLI z
`--credentials openclaw`. Wystarczy `asef`; nie trzeba wywoływać `asef_ksef`,
uruchamiać datowanych runnerów ani przeładowywać Gateway. Bezpośrednie uruchomienie
venv wymaga jawnego `/root/.openclaw/acli-skills/acli-ksef/.venv/bin/asef --credentials openclaw --json ...`.
Domyślny provider samego pakietu Python pozostaje `system` (keyring/systemd).

```sh
asef --json db path
asef --json profile list
asef --json auth check --nip NIP --env prod
asef --json invoice show DOCUMENT_ID
asef --json sync --nip NIP --env prod --from 2026-06-27T22:00:00Z
asef --json invoice status DOCUMENT_ID
asef --json invoice upo DOCUMENT_ID --out UPO.xml
```

NIP/ID i zakres synchronizacji muszą wynikać ze zlecenia; powyższa data jest
przykładem, nie zgodą na odświeżenie. `sync` w providerze `openclaw` korzysta z
paczek `/invoices/exports` i zapisanych kursorów PermanentStorage, nie pojedynczych
pobrań. `invoice show` jest lokalnym odczytem; `invoice status` odpytuje KSeF
i może zapisać status/UPO. `--json` zwraca pojedynczy wynik JSON i niezerowy kod
wyjścia przy błędzie.

Wysyłka nadal wymaga osobnego zatwierdzenia dokładnego XML oraz jawnego zlecenia.
CLI nie włącza `allowSend`; nie ma flagi omijającej tę politykę. Po właściwych
zgodach i autoryzowanej zmianie polityki:

```sh
asef --json invoice approve DOCUMENT_ID --sha256 HASH --yes
asef --json invoice send DOCUMENT_ID --sha256 HASH --confirm-prod HASH
```

PROD wymaga oddzielnego potwierdzenia tego środowiska. Timeout wysyłki oznacza
niepewny wynik: sprawdź status przed ponowną próbą. Nie interpretuj migracji CLI
jako zgody na zatwierdzenie, synchronizację lub wysłanie dokumentu.

CLI używa istniejących `profiles`, `pythonPath`, SecretRef i `allowSend` z
konfiguracji `asef-secretstore`, przez publiczny resolver SDK. Wtyczka pozostaje
włączona jako kontrakt poświadczeń; jej narzędzie czatowe jest opcjonalne.
Nie migruj tokenu. CLI jest przeznaczone dla zaufanego konta operatora na Gateway;
nie uwierzytelnia nadawcy czatu — agent musi ograniczyć użycie do prywatnego
właściciela lub autoryzowanego zlecenia ONYX. Brak sekretu/polityki kończy operację,
bez przełączania na inny magazyn. Sieciowy provider `openclaw` odrzuca ustawione
`ASEF_DATA_DIR`, by test/migracja nie użyły omyłkowo bazy produkcyjnej; lokalne
komendy nadal wspierają izolowany katalog. `auth set-token/use-token/use-certificate`
nie zmieniają magazynu w tym providerze. Pełny eksport bazy nie eksportuje sekretu
OpenClaw — po przeniesieniu odtwórz dostęp oddzielnie.

Instalacja provideru wymaga checkoutu z katalogiem `openclaw/`, Node.js oraz
dostępu do zainstalowanego SDK `openclaw/plugin-sdk`; sam wheel Pythona nie
zawiera SDK. Na obecnym Gateway zależności są już dostępne. Szczegóły protokołu
zawiera [README adaptera](../openclaw/README.md).

## Architektura

```text
Rozmowa z agentem OpenClaw
          │
          ▼
Agent OpenClaw + skill ASEF
          │
          ▼
ASEF CLI ── rdzeń ASEF ── lokalna SQLite i podglądy
                    └───── poświadczenie KSeF i API KSeF
```

Skill instruuje agenta, aby wywoływał `/usr/local/bin/asef --json` **na hoście Gateway**, z dostępem do tej samej bazy i magazynu poświadczeń. Baza i pliki podglądu są na Gateway; Mac nie jest źródłem danych podczas pracy agenta.

## Obecne wydanie ACLI

- Kod i środowisko Python są w `/root/.openclaw/acli-skills/acli-ksef/`; `/srv/asef` jest tylko aliasem zgodności dla istniejącej komendy i konfiguracji.
- Aktywna SQLite oraz wyniki są w `/root/.openclaw/acli-database/acli-ksef/`; dawny katalog danych jest aliasem do tego miejsca. Sprawdź `asef --json db path` i rozwiązaną ścieżkę przed pracą.
- Sprawdzony skill jest przypięty osobno w workspace ONYX i FOLIO. Oryginał kodu i stan są po pracy jednokierunkowo kopiowane do `storage-01/workspace/acli-skills/` i `storage-01/workspace/acli-database/`; SMB nie jest aktywnym plikiem SQLite ani niezależną kopią zapasową.
- Wysłanie rzeczywistej faktury wymaga osobnej zgody na konkretny dokument, hash XML i środowisko; migracja nie zmienia `allowSend=false`.

## Dane z Maca

Jeśli dotychczasowe dane ASEF są na Macu, wykonaj `asef db export asef-export.asef` i przenieś zaszyfrowane archiwum bezpiecznym kanałem na Gateway. Eksport obejmuje pełną bazę, dostępne tokeny KSeF i jawnie zarejestrowane pary certyfikat `.crt` + zaszyfrowany klucz prywatny `.key`. Wynik wskazuje liczbę certyfikatów oraz profile bez tokenu. Hasło archiwum (minimum 12 znaków przy eksporcie) podaje się wyłącznie w ukrytym monicie CLI, nigdy w rozmowie z agentem ani w argumentach polecenia. ASEF nie wyszukuje certyfikatów automatycznie; przed eksportem sprawdź `asef auth certificates` i w razie potrzeby użyj `asef auth add-certificate CERT KEY --purpose Authentication` (lub `--purpose Offline`).

Na nowym hoście z działającym `keyring`, bez `CREDENTIALS_DIRECTORY`, uruchom `asef db import asef-export.asef` z konta agenta i z pustym katalogiem danych. Import odtwarza dostępne tokeny w `keyring` oraz zarejestrowane certyfikaty i ich oryginalne zaszyfrowane pliki `.key` w prywatnym `ASEF_DATA_DIR/certificates`. Hasło samego klucza `.key` i wybór certyfikatu do logowania nie są zapisywane w archiwum. Po imporcie wybierz dla każdego profilu `asef auth use-certificate ODCISK --nip NIP --env ŚRODOWISKO` lub, jeśli token jest dostępny, `asef auth use-token --nip NIP --env ŚRODOWISKO`, a następnie wykonaj `asef auth check --nip NIP --env ŚRODOWISKO`. Import nie nadpisuje bazy, certyfikatów ani istniejących tokenów profili. Jeżeli docelowy proces ma ustawione `CREDENTIALS_DIRECTORY`, import archiwum z tokenami zakończy się przed odtworzeniem bazy: poświadczenia systemd trzeba wtedy przygotować osobno zgodnie z poniższą sekcją. Przed przełączeniem porównaj liczbę profili, dokumentów i certyfikatów oraz sprawdź przykładową fakturę. Nie uruchamiaj dwóch aktywnych baz jako równorzędnych źródeł faktur.

Do przenoszenia samych faktur służą `asef invoice export-bundle faktury.asefxml` i `asef invoice import-bundle faktury.asefxml`. Ten zaszyfrowany pakiet XML nie zawiera tokenów, certyfikatów, kluczy prywatnych, UPO ani całej historii bazy; import nie przenosi zatwierdzeń do wysyłki.

## Poświadczenia KSeF na serwerze

Jeżeli Gateway działa jako usługa systemd, zalecane jest `LoadCredentialEncrypted=` w jednostce tej usługi. Dla profilu wybierz **jedno** poświadczenie w `$CREDENTIALS_DIRECTORY`:

- `asef_<środowisko>_<NIP>`, np. `asef_prod_1234567890`, zawiera token KSeF;
- `asef_cert_<środowisko>_<NIP>`, np. `asef_cert_prod_1234567890`, zawiera chroniony dokument JSON z polami `fingerprint`, `password` (hasło zaszyfrowanego `.key`) i `subjectIdentifierType` (`certificateSubject` lub `certificateFingerprint`). Certyfikat o tym odcisku musi być wcześniej zarejestrowany w katalogu danych ASEF i mieć przeznaczenie `Authentication`.

ASEF wymaga zwykłego pliku dostępnego tylko dla właściciela. Jeśli dla profilu są oba pliki, odmawia logowania zamiast zgadywać metodę. Jeśli brakuje obu, zgłasza błąd bez przełączenia na `keyring`. Poświadczenia i token odświeżający nie trafiają do `openclaw.json`, argumentów procesu, rozmowy ani repozytorium; token odświeżający pozostaje w pamięci procesu CLI. Nazwę jednostki, konto usługi i ścieżkę zaszyfrowanego poświadczenia trzeba ustalić na rzeczywistym Gateway przed zmianą konfiguracji systemd.

Gdy `CREDENTIALS_DIRECTORY` nie jest ustawione, `asef auth set-token` zapisuje token w systemowym `keyring`, a `asef auth use-certificate ODCISK --nip NIP --env ŚRODOWISKO` pyta o hasło `.key` w ukrytym monicie i zapisuje je tam dla wybranego profilu. `asef auth use-token --nip NIP --env ŚRODOWISKO` przywraca logowanie tokenem. Na serwerze bez sesji graficznej najpierw zweryfikuj backend `keyring`, zamiast zakładać, że macOS Keychain działa na Linuksie. Przy poświadczeniu systemd metodę zmienia się przez chronione poświadczenia usługi, nie przez `auth use-certificate` lub `auth use-token`.

Certyfikat KSeF jest odrębnym poświadczeniem od tokenu. `asef auth add-certificate CERT KEY --purpose Authentication` (lub `--purpose Offline`) przyjmuje istniejącą parę `.crt` i zaszyfrowanego `.key` w formacie PEM PKCS#8 oraz pyta o hasło klucza lokalnie w ukrytym monicie. Zapisuje oryginalny klucz bez usuwania jego szyfrowania i bez przechowywania hasła. Po rejestracji sprawdź `asef auth certificates`, aby poznać odcisk. Według [dokumentacji KSeF](https://github.com/CIRFMF/ksef-api/blob/main/certyfikaty-KSeF.md) certyfikat `Offline` nie służy do uwierzytelniania. Próbę certyfikatu `Authentication` przeprowadzono na żywym KSeF TEST z losowym fikcyjnym NIP i samopodpisanym certyfikatem EC: ASEF uzyskał token dostępu i odświeżania. Nie jest to test rzeczywistego certyfikatu KSeF, DEMO, PROD ani wysyłki faktury. Nie umieszczaj pary certyfikat–klucz ani hasła w `openclaw.json`, repozytorium lub rozmowie z agentem.

## Podgląd i zgoda

`asef --json invoice preview ID --format pdf --out PLIK` zwraca ścieżkę **na Gateway** oraz SHA-256 XML. Agent ma przekazać użytkownikowi wygenerowany PDF lub HTML jako załącznik obsługiwany przez dany kanał OpenClaw. Sama ścieżka nie jest podglądem. Jeśli kanał nie pozwala pokazać pliku, agent zatrzymuje obieg przed zatwierdzeniem.

Po obejrzeniu podglądu użytkownik wyraźnie zatwierdza dokument i jego SHA-256. Agent może wtedy wykonać `asef invoice approve ID --sha256 HASH --yes`; późniejsze `asef invoice send ID` działa tylko dla tej wersji. PROD wymaga jeszcze osobnej zgody i `--confirm-prod HASH`. Przy niepewnym wyniku wysyłki agent sprawdza status, bez ponownej wysyłki.

SHA-256 chroni wersję XML, ale ASEF nie potwierdza tożsamości nadawcy wiadomości OpenClaw. Uprawnienia agenta, kanału i użytkownika są osobną granicą bezpieczeństwa na Gateway. Proces CLI potrzebuje dostępu do poświadczeń przy uwierzytelnianiu; ogranicz uprawnienia agenta i kanału, który może go uruchamiać.

Źródła: [OpenClaw skills](https://docs.openclaw.ai/skills), [OpenClaw skills CLI](https://docs.openclaw.ai/cli/skills), [załączniki](https://docs.openclaw.ai/help/faq/media-and-attachments), [systemd credentials](https://www.man7.org/linux/man-pages/man1/systemd-creds.1.html).
