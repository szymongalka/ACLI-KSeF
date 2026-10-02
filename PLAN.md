# ACLT-KSeF plan realizacji

Wersja 0.5. Data 2 października 2026. Status: projekt do przeglądu.

Autor projektu: **Szymon Gałka**.

Plan prowadzi od uzgodnienia [PRD](PRD.md) i [specyfikacji](SPECYFIKACJA.md) do skilla, którego cały zakres agent sprawnie obsługuje przez zawarte CLI w Pythonie na Linuxie z OpenClaw. Każdy etap obejmuje użyteczność dla agenta oraz poprawność wykonania. Daty realizacji zależą od potwierdzonego zakresu oraz dostępności serwera i poświadczeń.

## Stan prac

Dokumenty PRD, specyfikacja i plan są w wersji 0.5. Projekt ma lokalne repozytorium Git na gałęzi `main`, README, pełny tekst GNU GPLv3 w pliku LICENSE oraz reguły ignorowania lokalnych danych i poświadczeń. Uzgodniono nazwę ACLT-KSeF, autorstwo Szymona Gałki, licencję `GPL-3.0-only`, język Python, środowisko Linux z OpenClaw, pełny przepływ pobierania, wystawiania oraz korekt i priorytet szybkiej obsługi przez agenta. Szczegóły proponowanej architektury i pierwszych wariantów faktur pozostają do przeglądu. Implementacja skilla i CLI rozpocznie się po zamknięciu M0.

| Etap | Rezultat | Zależność | Stan |
| --- | --- | --- | --- |
| M0 | PRD, specyfikacja i plan uzgodnione | Decyzje D01–D03 | Dokumenty przygotowane do przeglądu |
| M1 | Instalowalna paczka skilla, CLI i logowanie tokenem | M0 | Zaplanowany |
| M2 | Odbiór, synchronizacja, przeglądanie i eksport | M1 | Zaplanowany |
| M3 | Przygotowanie i wysyłka faktury z numerem KSeF i UPO | M2 oraz D02 i D04 | Zaplanowany |
| M4 | Korekty | M3 | Zaplanowany |
| M5 | Certyfikat, PDF, kopia danych i odbiór z OpenClaw na Linuxie | M4 oraz D05 | Zaplanowany |
| M6 | Kontrolowane uruchomienie PROD | Odbiór TEST i DEMO, konfiguracja i uzgodnione operacje PROD | Zaplanowany |

Etap uważa się za odebrany po uzyskaniu opisanych dowodów. Zakończenie prac lokalnych bez dostępu do usługi jest raportowane jako zakończenie części lokalnej, nie jako pełny odbiór integracji.

[Materiały źródłowego acli-ksef](materials/acli-ksef/README.md) stanowią dodatkową inspirację. [Analiza](materials/acli-ksef/ANALIZA.md) wiąże przydatne rozwiązania i scenariusze testowe z etapami M1–M5. Obecność kodu w tym katalogu nie oznacza wykonania żadnego etapu implementacji ACLT-KSeF.

## M0 Uzgodnienie dokumentów

Rezultat: jedna spójna podstawa wykonania produktu.

Do uzgodnienia teraz:

1. Jeden operator i jeden NIP w pierwszym wdrożeniu, z osobnymi profilami TEST, DEMO i PROD.
2. Pierwszy generator obejmujący faktury krajowe w PLN i wskazane korekty; warianty potrzebne firmie mają pierwszeństwo przed proponowanym backlogiem.
3. Token jako pierwsza metoda logowania; certyfikat uwierzytelniający w zakresie docelowym, z możliwością wcześniejszej realizacji.

Dane firmy, numeracja, przypadki VAT oraz szczegóły serwera można uzupełnić przed etapami, które ich potrzebują. Odbiór M0 oznacza potwierdzenie architektury i pierwszego zakresu; dokumenty stają się wtedy wersją uzgodnioną.

## M1 Paczka skilla i pierwsza integracja

Rezultat: skill zawiera działające CLI, a token pozwala zalogować się do wybranego środowiska.

Prace:

1. Przygotować krótki `SKILL.md` z mapą wszystkich dostępnych zadań, launcher `scripts/aclt-ksef`, moduły Pythona, `pyproject.toml` i przypięte zależności. Cel instrukcji wejściowej to do 600 słów. Metadane, skill, pomoc i wersja CLI używają nazwy ACLT-KSeF oraz wskazują Szymona Gałkę jako autora projektu. Metadane paczki deklarują `GPL-3.0-only`, a wydanie zawiera pełny tekst LICENSE.
2. Dodać `doctor`, `describe`, profile, wspólny kontrakt JSON z `next_action` i obsługę błędów argumentów oraz plików. Opis komendy i parser korzystają z tych samych definicji.
3. Wprowadzić zewnętrzne katalogi konfiguracji i danych, SQLite oraz chroniony magazyn poświadczeń i stanu logowania.
4. Zapisem źródła, wersji i SHA-256 przypiąć używany kontrakt API; sprawdzić wymagane operacje właściwego środowiska.
5. Zrealizować logowanie istniejącym tokenem KSeF, sprawdzanie stanu i odświeżanie dostępu. Sprawdzić odzyskiwanie niepewnej operacji odebrania tokenów.
6. Przeprowadzić próbę na TEST z poświadczeniem dostarczonym przez operatora. Ocenić wcześnie wymagania podpisu certyfikatem i zgodność kandydata biblioteki, aby ryzyko XAdES było znane przed M5.

Odbiór:

- CLI uruchamia się z innego katalogu roboczego i używa środowiska paczki; brak `.venv` daje czytelny błąd.
- Nazwa i autorstwo są spójne z README oraz specyfikacją; branding nie dodaje tekstu poza kontraktem JSON.
- Licencja w metadanych to `GPL-3.0-only`; paczka zawiera LICENSE i wymagane oznaczenia wykorzystanych zależności oraz zasobów.
- Każda dostępna funkcja jest odkrywalna przez mapę skilla i `describe`; typowa czynność nie wymaga czytania dokumentacji projektowej. Agent nie wykonuje diagnostyki ani osobnego logowania przed każdą komendą.
- `--json` pozostaje poprawnym JSON również przy błędnej składni i brakującym pliku.
- Konfiguracja TEST i PROD jest odseparowana; wartości sekretów nie występują w wynikach ani logach.
- Lokalne testy transportu obejmują logowanie, odnowienie, brak uprawnień i niepewne odebranie tokenów.
- Osobny zapis próby TEST potwierdza rzeczywiste logowanie. Bez poświadczeń ten punkt pozostaje otwarty.

To pierwszy etap implementacyjny po uzgodnieniu dokumentów. Jego celem jest działająca paczka i potwierdzony dostęp do API.

## M2 Pobieranie i pierwsza wersja użytkowa

Rezultat: operator przez skilla pobiera faktury, wyszukuje je lokalnie i uzyskuje oryginalne XML oraz podgląd.

Prace:

1. Zbudować kontrolny zbiór syntetycznych dokumentów i metadanych obejmujących skonfigurowane role podmiotu oraz dokument widoczny w kilku rolach.
2. Zrealizować asynchroniczny eksport paczek, pobranie, odszyfrowanie, bezpieczne rozpakowanie i import.
3. Dodać osobne punkty kontynuacji per rola, prawidłowe granice dla paczek pełnych i obciętych oraz deduplikację.
4. Utrwalać postęp po zapisaniu paczki; wznowić przerwany eksport z istniejącej referencji.
5. Dodać `invoices list`, `show`, `fetch` oraz eksport XML, HTML i CSV. `list --refresh` łączy synchronizację i filtrowanie. Listy pokazują aktualność, rolę i kompletność zbioru, domyślnie do 20 rekordów.
6. Przypiąć schemy FA(3) z importami i przygotować podgląd odczytanego XML z brandingiem ACLT-KSeF oraz informacją o autorze projektu; zakres odczytu może być szerszy od przyszłego generatora.
7. Sprawdzić workflow przez instrukcję skilla: synchronizacja, wyszukiwanie, pokazanie dokumentu i przekazanie ścieżki artefaktu.

Odbiór:

- Numery KSeF pobranych dokumentów odpowiadają zbiorowi kontrolnemu dla zadeklarowanych ról.
- Ponowne uruchomienie i awarie w trakcie zapisu nie gubią faktur i nie tworzą powtórzeń.
- Test granicy obciętej paczki wykrywa użycie niewłaściwego punktu kontynuacji.
- HTTP 429 nie traci postępu i daje kontrolowany wynik z czasem ponowienia.
- XML pozostaje niezmieniony, podgląd pokazuje ograniczenia odczytu, a CSV nie wykonuje tekstu faktury jako formuły.
- Lokalne testy i rzeczywista próba pobierania TEST są raportowane osobno.
- Przygotować odtwarzalny pomiar strony 20 rekordów w bazie 10 tysięcy faktur, obejmujący start CLI. Ocenić cel P95 poniżej sekundy na uzgodnionym serwerze; zapisać liczbę wywołań i objętość wyników dla kontrolnych zadań odczytu.

Dokumentacja MF wskazuje eksport paczek i datę trwałego zapisu jako podstawę synchronizacji przyrostowej. Ten mechanizm jest częścią odbioru, a nie optymalizacją dodawaną po pierwszej wersji. [Synchronizacja MF](https://github.com/CIRFMF/ksef-api/blob/main/pobieranie-faktur/przyrostowe-pobieranie-faktur.md).

## M3 Wystawianie i kontrolowana wysyłka

Rezultat: uzgodniony typ faktury przechodzi cały przepływ od szkicu do numeru KSeF i zapisanego UPO.

Prace:

1. Ustalić schemat wejścia JSON, numerację, obsługiwane stawki i adnotacje, reguły zaokrągleń oraz przykłady oczekiwanych kwot. Dane dostarcza operator.
2. Zrealizować szkice i ich niezmienne wersje, obliczenia `Decimal`, generator XML oraz import gotowych XML w zakresie wiernego podglądu.
3. Dodać walidację danych, XSD i sum oraz podgląd wygenerowany z utrwalonego XML. Udostępnić je wraz z przygotowaniem szkicu przez `drafts prepare`, zwracające wszystkie wykryte braki danych razem.
4. Wprowadzić zatwierdzenie związane ze skrótem XML, ID, wersją i kontekstem; skill przedstawia dokument i pozyskuje zgodę. `drafts send --xml-sha256` zapisuje zgodę i rozpoczyna wysyłkę w jednym wywołaniu po decyzji operatora.
5. Zrealizować sesję online, szyfrowanie, trwałe zapisanie stanu przed wysyłką, sprawdzanie statusu, zamknięcie sesji i pobranie UPO.
6. Dodać historię zdarzeń, ochronę między procesami i odzyskiwanie niepewnych wysyłek.
7. Przeprowadzić kontrolowany pełny przebieg TEST na uzgodnionych syntetycznych przykładach.

Odbiór:

- Uzgodnione przykłady finansowe dają oczekiwane kwoty; błędne wejście nie tworzy dokumentu gotowego do wysyłki.
- Podgląd i XML przedstawiają tę samą wersję; zmiana bajtów XML lub profilu blokuje wcześniejszą zgodę.
- Dwa równoległe procesy nie rozpoczynają dwóch prób wysyłki tej samej wersji.
- Jednoznaczne odrzucenie oraz zerwane połączenie prowadzą do różnych, poprawnych stanów; niepewny wynik nie uruchamia automatycznej ponownej wysyłki.
- Próba TEST potwierdza przyjęcie, numer KSeF i UPO. Dodatkowo sprawdzono odrzucenie, awarię oraz poprawne odzyskanie stanu.
- Wynik `processing` lub zakończona odpowiedź HTTP nie jest prezentowana jako przyjęcie faktury.
- Dla poprawnego wejścia przygotowanie z walidacją i podglądem wymaga jednego wywołania, a zatwierdzenie i rozpoczęcie wysyłki po zgodzie operatora kolejnego. Sprawdzenie operacji zachowuje dostępne UPO bez osobnego wywołania przez agenta.

## M4 Korekty

Rezultat: operator przygotowuje i wysyła uzgodnione rodzaje korekt do zaakceptowanych faktur źródłowych.

Prace:

1. Uzgodnić konkretne przykłady korekty ceny, ilości i danych formalnych, w tym obniżenie wartości.
2. Przygotować model zmian, powiązanie ze źródłem oraz mapowanie na właściwe pola FA(3).
3. Przez `corrections prepare` przygotować i zwalidować korektę oraz pokazać w podglądzie powód, zakres zmian, wartości źródłowe i wynik w jednym wywołaniu.
4. Wykorzystać istniejący proces walidacji, zatwierdzenia, wysyłki i UPO.
5. Przeprowadzić przyjęcie kontrolnych korekt na TEST.

Odbiór: źródło pozostaje niezmienione, korekta ma własny numer i wersję, sumy odpowiadają przypadkom kontrolnym, a przyjęcie TEST oraz UPO są udokumentowane. Warianty poza uzgodnionym katalogiem są jawnie zgłaszane jako nieobsługiwane.

## M5 Gotowość operacyjna na Linuxie

Rezultat: kompletna paczka działa przez docelowego agenta OpenClaw, obsługuje logowanie certyfikatem, dokumenty PDF i odtworzenie kopii.

Prace:

1. Zweryfikować wersję OpenClaw, dystrybucję i architekturę Linuxa, użytkownika procesu oraz faktyczne środowisko `exec`.
2. Zainstalować paczkę i zależności na serwerze, dopasować ścieżki i sprawdzić widoczność skilla oraz dostęp do CLI, danych i poświadczeń.
3. Zrealizować i sprawdzić logowanie certyfikatem uwierzytelniającym, w tym błędy ważności i uprawnień. Udokumentować wynik wybranego stosu XAdES.
4. Dodać PDF z polskimi znakami, brandingiem ACLT-KSeF, informacją o autorze projektu i kodem QR właściwego środowiska dla przyjętych dokumentów. Sprawdzić długie i wielostronicowe faktury oraz korekty.
5. Dodać spójną kopię danych i manifest, odtworzenie do nowego katalogu oraz rozliczenie operacji zachowanych w kopii.
6. Sprawdzić restart, aktualizację paczki z zachowaniem danych i odtworzenie poprzedniej wersji programu zgodnie z wersją bazy.
7. Sprawdzić realne zadania w nowej sesji agenta OpenClaw, obejmujące odczyt, przygotowanie faktury, korektę, braki danych i odzyskiwanie operacji. Mierzyć poprawność, liczbę wywołań i ilość kontekstu. Sprawdzić logowanie i uprawnienia na DEMO, gdy operator udostępni poświadczenia.

Odbiór:

- Agent korzysta z CLI znajdującego się w skillu i poprawnie odczytuje JSON oraz stany operacji.
- Agent zna drogę do każdej dostępnej funkcji, realizuje typowe zadania w budżecie wywołań z PRD i korzysta z `next_action` przy brakach danych lub oczekiwaniu. Raport zawiera także użyty model, rozmiar instrukcji i wyników oraz czasy lokalnych odczytów.
- TEST obejmuje cały uzgodniony przepływ uruchomiony z docelowego Linuxa; wynik lokalnego uruchomienia na Macu nie zastępuje tego odbioru.
- Rzeczywisty certyfikat i uprawnienia zostały sprawdzone na DEMO. Sam certyfikat self-signed TEST nie zamyka tego punktu.
- PDF jest obejrzany, jego kwoty odpowiadają XML, a link z QR odpowiada danym i środowisku dokumentu.
- Odtworzona kopia pozwala odczytać faktury, XML, UPO i historię; wznowienie niepewnej operacji nie tworzy nowej wysyłki.

Widoczność skilla, jego gotowość i dostępność w danej sesji OpenClaw wymagają osobnej weryfikacji. [Dokumentacja skilli OpenClaw](https://docs.openclaw.ai/tools/skills).

## M6 Kontrolowane uruchomienie produkcyjne

Rezultat: uzgodnione czynności działają z rzeczywistymi uprawnieniami w PROD, z zapisanymi dowodami wyniku.

Kolejność:

1. Sprawdzić aktualny kontrakt PROD, poświadczenia, kontekst NIP oraz zgodność konfiguracji z zakończonym odbiorem DEMO.
2. Wykonać uzgodniony odczyt i synchronizację bez uruchamiania zapisu faktur.
3. Przygotować wskazany przez operatora rzeczywisty dokument, przedstawić podgląd i środowisko oraz uzyskać zgodę na dokładną wersję.
4. Wysłać zatwierdzony dokument, rozstrzygnąć jego stan i zachować numer KSeF oraz UPO.
5. Ustalić zasady kolejnych operacji, kopii danych i ewentualnej cyklicznej synchronizacji. Harmonogram dodajemy dopiero po uzgodnieniu potrzeby i częstotliwości.

Odbiór PROD obejmuje tylko faktycznie przeprowadzone przypadki. Poprawna faktura nie potwierdza automatycznie wszystkich wariantów korekt lub wszystkich metod uwierzytelniania. Operacje PROD wymagają rzeczywistych poświadczeń i zgody operatora dotyczącej wykonywanego dokumentu; przygotowanie tych dokumentów projektowych nie stanowi takiej zgody. [Środowisko produkcyjne MF](https://ksef.podatki.gov.pl/ksef-na-okres-obligatoryjny/wsparcie-dla-integratorow/).

## Powiązanie wymagań z etapami

| Wymagania PRD | Etapy realizacji |
| --- | --- |
| F01–F02, Q01, Q05 | M1, potwierdzenie wdrożenia w M5 |
| F03 | Token w M1, certyfikat i rzeczywiste uprawnienia w M5 |
| F04–F05, Q02, Q06 | M2, ponownie sprawdzane dla wysyłki w M3 |
| F06, Q08 | XML, HTML i CSV w M2; PDF i QR w M5 |
| F07–F10, Q03 | M3 |
| F11 | M4 |
| F12 | Synchronizacja w M2, wysyłka w M3, odtworzenie w M5 |
| F13 | Historia w M3, kopia i odtworzenie w M5 |
| F14, Q09 | Opis funkcji i krótki skill w M1, komendy zadaniowe w M2–M4, pełny odbiór przez agenta i pomiary w M5 |
| Q10 | Dokumentacja i repozytorium w M0, skill i CLI w M1, HTML w M2, PDF w M5 |
| Q04, Q07 | Przez wszystkie etapy, odbiór na Linuxie w M5 |

## Ryzyka i sposób ich rozstrzygnięcia

| Ryzyko | Działanie i termin |
| --- | --- |
| Nieznany katalog faktur potrzebnych firmie | Uzgodnić D02 przed M3; przesunąć wymagane warianty z backlogu do zakresu. |
| Brak poświadczeń TEST lub dostępu do serwera | Oddzielić zakończenie części lokalnej od otwartego odbioru integracji. |
| Pythonowa biblioteka XAdES nie spełnia wymagań KSeF | Wczesna ocena w M1, próba zgodności przed wdrożeniem certyfikatu; aktualizacja specyfikacji na podstawie wyniku. |
| Różnice między środowiskami i aktualizacje API | Przypiąć kontrakt użyty w testach i sprawdzać docelowe środowisko przed jego odbiorem. |
| Pominięcie danych na granicy eksportu | Obowiązkowe testy pełnych i obciętych paczek oraz awarii przed przesunięciem kontynuacji. |
| Podwójne wysłanie po awarii | Trwały stan przed wysyłką, atomowa ochrona między procesami i rozstrzyganie istniejącej operacji. |
| Niepełny podgląd XML | Zablokować zatwierdzenie wariantu, którego istotnych danych nie można wiernie pokazać. |
| Inne ścieżki i uprawnienia w kontenerze OpenClaw | Zweryfikować faktyczne środowisko wykonawcze i wykonać odbiór przez docelowego agenta. |

## Dowody zakończenia etapu

Raport odbioru zapisuje wersję paczki, kontraktu API i schem, środowisko, wykonane komendy, wyniki testów, identyfikatory operacji oraz ograniczenia pokrycia. Zachowuje referencje i skróty artefaktów, bez sekretów i zbędnych danych faktur w logach. Dla dokumentów wysyłanych wskazuje, czy uzyskano przyjęcie, numer KSeF i UPO.

Przyrost funkcjonalności aktualizuje równocześnie mapę skilla i opis dostępnych komend oraz sprawdza liczbę wywołań potrzebnych do nowego zadania. Testy rozszerzamy o nowy przypadek lub ujawnione ryzyko, zamiast mnożyć testy powtarzające implementację. Powrót do starszej paczki wymaga sprawdzenia zgodności wersji bazy; migracji nie cofamy przez podmianę kodu w ciemno.

Kolejny krok po przeglądzie dokumentów to zamknięcie decyzji D01–D03 i rozpoczęcie M1. Warianty faktur i informacje serwerowe zbieramy przed etapem, który ich wymaga.
