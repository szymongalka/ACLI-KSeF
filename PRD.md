# ACLT-KSeF PRD

Wersja 0.5. Data 2 października 2026. Status: projekt do przeglądu.

Autor projektu: **Szymon Gałka**.

Kontakt: [kontakt@szymongalka.dev](mailto:kontakt@szymongalka.dev).

Głównym celem ACLT-KSeF jest szybka i sprawna obsługa całego zakresu skilla przez agenta AI. Produktem jest jedna paczka skilla z krótką instrukcją i własnym CLI w Pythonie, które wykonuje kompletne zadania użytkownika. Docelowe środowisko to serwer Linux z OpenClaw. Ten dokument określa potrzeby użytkownika i kryteria odbioru; [specyfikacja](SPECYFIKACJA.md) opisuje wykonanie, a [plan](PLAN.md) kolejność prac.

## Ustalenia użytkownika

| Obszar | Ustalenie |
| --- | --- |
| Lokalizacja projektu | `/Users/szymongalka/ACLT-KSeF` |
| Nazwa produktu | ACLT-KSeF |
| Autor i twórca projektu | Szymon Gałka |
| Kontakt autora | `kontakt@szymongalka.dev` |
| Licencja projektu | GNU GPLv3, identyfikator SPDX `GPL-3.0-only` |
| Produkt | Skill dla AI z CLI zawartym w paczce skilla |
| Technologia CLI | Python |
| Środowisko docelowe | Linux, głównie OpenClaw |
| Zakres docelowy | Pobieranie oraz wystawianie faktur i korekt |
| Główny priorytet | Agent szybko odnajduje i sprawnie obsługuje wszystkie dostępne funkcje skilla. |
| Sposób realizacji | Etapami, zaczynając od PRD, specyfikacji i planu |
| Materiał dodatkowy | Źródłowy `acli-ksef`/ASEF służy jako inspiracja; rozwiązania wybieramy zgodnie z wymaganiami ACLT-KSeF. |

Pozostałe wybory w tych dokumentach są propozycjami projektowymi. W szczególności jeden operator, jeden NIP i zakres pierwszych wystawianych dokumentów są założeniami do potwierdzenia, a nie dodatkowymi ustaleniami użytkownika.

## Branding i autorstwo

Nazwa produktu to **ACLT-KSeF**. Identyfikator skilla, paczki i komendy CLI to `aclt-ksef`. Dokumentacja, instrukcja skilla, pomoc CLI oraz podglądy HTML i PDF przedstawiają nazwę produktu i informację „Autor projektu: Szymon Gałka”. Projekt stosuje spójne nazewnictwo także w metadanych wydań.

W informacjach o autorstwie stosujemy zawsze formę **Szymon Gałka**, bez odmiany nazwiska. Adres kontaktowy autora to `kontakt@szymongalka.dev`.

Branding podglądu identyfikuje narzędzie, które go wygenerowało. Dane sprzedawcy, nabywcy, numer dokumentu i status KSeF pozostają odrębnymi informacjami pochodzącymi z dokumentu. Informacja o autorze programu nie zastępuje danych wystawcy faktury.

Kod i dokumentacja projektu są udostępniane na GNU GPLv3 (`GPL-3.0-only`). Pełny tekst licencji znajduje się w [LICENSE](LICENSE) i jest dostarczany z paczką skilla.

## Problem i cel

Agent powinien po przeczytaniu krótkiej instrukcji wybrać właściwą operację, przekazać dane i otrzymać wynik wystarczający do odpowiedzi użytkownikowi. Liczbę wywołań CLI, ilość ładowanej dokumentacji i objętość wyników traktujemy jako cechy produktu. Agent nie powinien odkrywać składni przez serię błędów ani ręcznie składać kroków technicznych API.

Logowanie, synchronizacja, walidacja, obliczenia i stan wysyłki są obsługiwane przez kod. Użytkownik otrzymuje czytelny podgląd dokumentu oraz jednoznaczną informację, czy KSeF go przyjął.

Skill odpowiada za rozmowę, wybór operacji i przedstawienie wyniku. CLI odpowiada za dane i wykonanie. Użytkownik może uruchomić to samo CLI ręcznie, bez pośrednictwa modelu. Wynik działania nie powinien zależeć od sposobu sformułowania odpowiedzi przez AI.

## Użytkownicy i sposób użycia

Podstawowym użytkownikiem jest operator firmy rozmawiający z agentem OpenClaw. Operator serwera instaluje paczkę i konfiguruje dostęp do KSeF. Role te może pełnić jedna osoba.

Proponowany zakres pierwszego wdrożenia to jeden operator i jeden NIP. Osobne profile tego samego NIP pozwalają rozdzielić TEST, DEMO i PROD. Obsługa wielu firm może zostać dodana po potwierdzeniu takiej potrzeby; nie wymaga budowy usługi dla wielu użytkowników.

Przykładowe prośby:

- „Pobierz nowe faktury i pokaż zakupy z września”.
- „Znajdź fakturę od tego kontrahenta i zapisz XML”.
- „Przygotuj fakturę za te usługi i pokaż podgląd”.
- „Popraw cenę na tej fakturze i przygotuj korektę”.
- „Sprawdź, czy zatwierdzona faktura została przyjęta, i pobierz UPO”.

Daty wystawienia, przyjęcia w KSeF i lokalnego pobrania muszą być rozróżniane. Zestawienie za miesiąc ma podawać, według której daty zostało przygotowane.

## Sprawna obsługa przez agenta

`SKILL.md` zawiera mapę wszystkich dostępnych zadań, sposób wywołania CLI i reguły interpretacji wyników. Szczegółowy opis pól wejścia jest dostępny na żądanie. Typowa czynność nie wymaga czytania PRD, specyfikacji technicznej, źródeł programu ani dokumentacji API KSeF.

CLI automatycznie korzysta z zapisanego dostępu i odnawia go, kiedy to potrzebne. Przygotowanie dokumentu obejmuje walidację i podgląd w jednym wywołaniu. Wynik przedstawia wszystkie wykryte braki danych razem oraz wskazuje następny dozwolony krok. Listy są ograniczone i korzystają z lokalnego zbioru; odświeżenie odbywa się na wyraźne żądanie.

Proponowane cele odbioru dla skonfigurowanej instalacji i poprawnych danych:

| Zadanie | Docelowa liczba wywołań CLI przez agenta |
| --- | --- |
| Wyświetlenie istniejących faktur lub szczegółów dokumentu | 1 |
| Pobranie nowych faktur i pokazanie odfiltrowanej listy | 1, jeśli synchronizacja zakończy się w budżecie czasu komendy |
| Przygotowanie faktury albo korekty z walidacją i podglądem | 1 |
| Zatwierdzenie dokładnej wersji i rozpoczęcie wysyłki po zgodzie operatora | 1 |
| Sprawdzenie zapisanej operacji i zachowanie dostępnego UPO | 1 |

Oczekiwanie na KSeF, uzupełnienie danych i rozmowa o zgodzie mogą wymagać dalszych kroków. Cele nie ograniczają obsługi błędów ani kontroli wersji dokumentu. Podczas odbioru mierzymy także rozmiar instrukcji i wyników oraz czas lokalnych odczytów.

## Zakres funkcjonalny

| ID | Wymaganie | Oczekiwany wynik |
| --- | --- | --- |
| F01 | Dostarczenie skilla z CLI | Jedna paczka zawiera `SKILL.md`, kod CLI i opis zależności; agent uruchamia CLI z tej paczki. |
| F02 | Profile i diagnostyka | Operator widzi środowisko, NIP, stan konfiguracji i gotowość do operacji. |
| F03 | Uwierzytelnianie | Logowanie tokenem KSeF oraz docelowo certyfikatem uwierzytelniającym; odnowienie sesji i czytelne błędy uprawnień. |
| F04 | Synchronizacja | Pobranie dostępnych faktur wraz z oryginalnymi XML i metadanymi; wznowienie po przerwaniu. |
| F05 | Wyszukiwanie i listy | Wystawione, otrzymane i korekty; filtry po datach, numerach, NIP i kwotach; informacje o aktualności zbioru. |
| F06 | Odczyt i eksport | Szczegóły faktury, oryginalny XML, podgląd HTML, docelowo PDF oraz zestawienie CSV. |
| F07 | Przygotowanie faktury | Ustrukturyzowany szkic z jawnie podanymi danymi, obliczeniami i numerem dokumentu. |
| F08 | Walidacja i podgląd | Walidacja danych i XML FA(3), sprawdzenie sum, podgląd dokładnej wersji przeznaczonej do wysyłki. |
| F09 | Zatwierdzenie | Zgoda operatora na konkretną wersję dokumentu i konkretne środowisko KSeF. |
| F10 | Wysyłka i potwierdzenia | Wysyłka online, sprawdzanie statusu, numer KSeF, pobranie i zachowanie UPO. |
| F11 | Korekty | Osobny dokument związany z fakturą źródłową, pokazujący zakres zmiany oraz jej wpływ na kwoty. |
| F12 | Odzyskiwanie stanu | Rozstrzygnięcie niepewnej wysyłki, ochrona przed równoległym wysłaniem tej samej wersji i utratą postępu synchronizacji. |
| F13 | Historia operacji i kopia danych | Rejestr zmian stanu, zatwierdzeń i referencji KSeF; kopia i sprawdzone odtworzenie danych. |
| F14 | Odkrywanie funkcji przez agenta | Zwięzły katalog dostępnych operacji oraz opis wymaganych pól konkretnej komendy w JSON. |

Integracja będzie oparta na API KSeF 2.0 i FA(3). MF publikuje kontrakty API oraz schemat faktury. [Dokumentacja integracyjna MF](https://ksef.podatki.gov.pl/ksef-na-okres-obligatoryjny/wsparcie-dla-integratorow/), [struktura FA(3)](https://ksef.podatki.gov.pl/informacje-ogolne-ksef-20/struktura-logiczna-fa-3/).

## Zakres pierwszej wersji użytkowej

Pierwsza wersja użytkowa obejmie działający odbiór i przeglądanie faktur. Kolejne etapy dodadzą wystawianie, wysyłkę i korekty. Pełny przepływ obsługi dokumentu nie oznacza automatycznie obsługi wszystkich wariantów podatkowych FA(3).

Propozycja pierwszego zakresu wystawiania:

- Faktury krajowe w PLN wystawiane we własnym imieniu, z pozycjami towarów lub usług.
- Jawne stawki i oznaczenia VAT wybrane przez operatora, w zakresie zatwierdzonego katalogu przypadków.
- Korekty ceny, ilości i wskazanych danych formalnych do takich faktur; także korekty zmniejszające wartość.
- Import gotowego XML FA(3) przechodzącego ten sam proces walidacji, podglądu i zatwierdzenia. Zakres importu może być szerszy od zakresu generatora tylko wtedy, gdy podgląd wiernie pokazuje jego istotne pola.

Pobieranie zachowuje oryginalne XML także wtedy, gdy generator nie obsługuje danego rodzaju dokumentu. Podgląd zgłasza ograniczenia interpretacji; nie pomija istotnych danych bez ostrzeżenia. Nieobsługiwany wariant wystawiania zwraca jednoznaczną informację o ograniczeniu.

## Przepływy użytkownika

### Pobieranie i przeglądanie

Operator wybiera profil i zleca synchronizację. CLI zapisuje dokumenty, metadane i postęp pobierania. Agent przedstawia wynik: nowe dokumenty, powtórzenia, błędy, zakres ról podmiotu i czas ostatniej zakończonej synchronizacji. Następne wyszukiwania korzystają z lokalnych danych. Agent informuje, jeśli zestaw jest nieaktualny lub niekompletny.

### Wystawianie

Agent zbiera dane od operatora. Brak obowiązkowych danych powoduje prośbę o ich uzupełnienie. CLI tworzy szkic, oblicza kwoty, generuje XML i podgląd. Operator widzi kontrahenta, pozycje, oznaczenia, kwoty, daty, numer oraz środowisko wysyłki. Po wyraźnej zgodzie zatwierdzana jest dokładna wersja XML. Zmiana dokumentu unieważnia jej zatwierdzenie.

Wysyłka zwraca stan operacji i referencje. Agent odróżnia przekazanie dokumentu od jego przyjęcia. Przepływ kończy się sprawdzonym statusem KSeF, numerem i zapisanym UPO; oczekiwanie na UPO jest osobnym stanem.

### Korekta

Operator wskazuje fakturę źródłową i zmianę. CLI zachowuje dokument źródłowy, tworzy nowy szkic korekty i przedstawia porównanie. Korekta przechodzi taki sam proces zatwierdzenia i wysyłki jak zwykła faktura.

### Przerwana operacja

Po przerwaniu synchronizacji ponowne uruchomienie wznawia pobieranie bez utraty dokumentów. Po utracie odpowiedzi na wysyłkę agent sprawdza istniejącą operację. Nie zgaduje, czy można ponowić wysłanie.

## Wymagania jakościowe

| ID | Wymaganie | Kryterium |
| --- | --- | --- |
| Q01 | Przewidywalność dla AI | Wersjonowany JSON, stabilne kody błędów i kod zakończenia procesu; brak tekstu diagnostycznego w strumieniu JSON. |
| Q02 | Trwałość | Restart i awaria procesu nie usuwają zapisanych dokumentów, zatwierdzeń ani referencji operacji. |
| Q03 | Poprawność kwot | Obliczenia dziesiętne i jawne reguły zaokrągleń, sprawdzone na uzgodnionych przypadkach. |
| Q04 | Poufność | Dane i sekrety poza paczką skilla; sekrety nie trafiają do rozmowy, argumentów poleceń ani logów. |
| Q05 | Oddzielenie środowisk | Dokumenty, poświadczenia, zatwierdzenia i postęp pobierania przypisane do profilu i środowiska. |
| Q06 | Ograniczenie skutków błędu | Obsługa limitów API, kontrolowane ponawianie odczytu i brak automatycznego ponowienia niepewnej wysyłki. |
| Q07 | Utrzymanie | Odtwarzalna instalacja zależności, aktualizacja skilla zachowująca dane, działanie na docelowym Linuxie. |
| Q08 | Czytelność | Podgląd zgodny z XML, poprawne polskie znaki, wielostronicowe PDF bez utraty pozycji; brak wymyślonego statusu płatności. |
| Q09 | Szybkość obsługi przez AI | Krótka instrukcja wejściowa, ograniczone wyniki i typowe zadania realizowane w liczbie wywołań określonej powyżej. |
| Q10 | Branding i autorstwo | Spójna nazwa ACLT-KSeF oraz podpis „Autor projektu: Szymon Gałka” i kontakt `kontakt@szymongalka.dev` w dokumentacji, skillu, pomocy CLI i podglądach. |

Zawartość faktur i opisy kontrahentów stanowią dane wejściowe, a nie instrukcje dla agenta. Model nie ustala samodzielnie zasad podatkowych, nie dobiera stawki bez danych i nie przedstawia lokalnego oznaczenia płatności jako informacji pochodzącej z KSeF.

## Granice pierwszego wydania

Pierwsze wydanie skupia się na obsłudze online jednego operatora. Backlog obejmuje tryby offline i awaryjne, sesje wsadowe do masowej wysyłki, załączniki strukturalne, faktury zaliczkowe i rozliczeniowe, waluty obce, WDT i eksport, marżę, samofakturowanie oraz role JST i grup VAT. Wymagany wariant z tej listy należy włączyć do zakresu przed implementacją generatora.

Integracje GUS, biała lista VAT, rejestry księgowe, płatności bankowe, automatyczna wysyłka cykliczna i panel WWW pozostają osobnymi rozszerzeniami. Paczka nie potrzebuje osobnej usługi HTTP ani MCP do realizacji uzgodnionego przepływu.

## Kryteria odbioru

- **Odbiór paczki:** po instalacji ze wskazanego katalogu skill wywołuje zawarte CLI, które działa niezależnie od bieżącego katalogu terminala.
- **Odbiór brandingu:** paczka i dokumentacja używają nazwy ACLT-KSeF; skill, pomoc CLI oraz podglądy wskazują autora projektu. Branding nie zmienia oryginalnego XML ani danych stron faktury.
- **Odbiór obsługi przez AI:** agent w nowej sesji, z instrukcją skilla i przygotowaną konfiguracją, realizuje reprezentatywne zadania odczytu, wystawiania, korekty i sprawdzania stanu; korzysta z opisu pól tylko wtedy, gdy go potrzebuje, i mieści się w zakładanej liczbie wywołań dla poprawnych danych. Każda dostępna funkcja ma jednoznaczną drogę odkrycia.
- **Odbiór pobierania:** kontrolny zbiór TEST jest kompletny dla zadeklarowanych ról; ponowna i przerwana synchronizacja nie pomija dokumentów i nie tworzy duplikatów.
- **Odbiór wystawiania:** uzgodnione przypadki dają poprawne sumy, XML przechodzi przypięte XSD, a operator widzi tę samą wersję danych w podglądzie.
- **Odbiór zgody:** zmiana XML, profilu lub środowiska blokuje wykorzystanie wcześniejszego zatwierdzenia.
- **Odbiór wysyłki:** na TEST udokumentowano cały przebieg od szkicu do numeru KSeF i UPO, w tym odrzucenie oraz niepewny wynik wysyłki.
- **Odbiór korekt:** przykłady uzgodnionych korekt zostają przyjęte na TEST i zachowują powiązanie ze źródłem.
- **Odbiór wdrożenia:** te same operacje działają przez agenta OpenClaw na docelowym Linuxie; odtworzenie kopii danych jest sprawdzone.

Testy lokalne, integracja TEST, walidacja DEMO i działanie PROD stanowią odrębne poziomy dowodów. TEST służy do testów integracji, DEMO wymaga rzeczywistych uprawnień, a faktury w PROD wywołują skutki prawne. [Opis środowisk MF](https://ksef.podatki.gov.pl/ksef-na-okres-obligatoryjny/wsparcie-dla-integratorow/).

## Decyzje do potwierdzenia

| ID | Propozycja | Kiedy potrzebna jest decyzja |
| --- | --- | --- |
| D01 | Jeden operator i jeden NIP w pierwszym wdrożeniu | Przed uzgodnieniem dokumentów. |
| D02 | Pierwszy generator: faktury krajowe PLN i opisane korekty | Ogólny zakres w M0, szczegółowe warianty przed projektowaniem danych faktury w M3. |
| D03 | Token jako pierwsza metoda logowania; certyfikat przed zamknięciem zakresu 1.0 | Przed M1; termin certyfikatu można przyspieszyć, jeśli operator już go używa. |
| D04 | Dane firmy, schemat numeracji oraz katalog przypadków VAT dostarcza operator | Przed M3. |
| D05 | Wersja OpenClaw, dystrybucja i architektura Linuxa oraz miejsce wykonywania `exec` | Przed odbiorem na serwerze w M5. |

Aktualna strona MF informuje o decyzji utrzymania tokenów bezterminowo i zapowiadanej zmianie rozporządzenia. Proponowana architektura obsługuje token i certyfikat; nie zakłada automatycznego wyłączenia tokenów w 2027 roku. Stan wymagań należy sprawdzić ponownie przed wdrożeniem PROD. [Certyfikaty i tokeny MF](https://ksef.podatki.gov.pl/informacje-ogolne-ksef-20/certyfikaty-ksef/).
