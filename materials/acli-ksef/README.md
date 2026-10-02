# acli-ksef / ASEF — materiał inspiracyjny

Ten katalog przechowuje dodatkowy materiał do projektowania **ACLT-KSeF**. Zgodnie z ustaleniem użytkownika źródłowy `acli-ksef` służy jako inspiracja. Snapshot zawiera historyczny kod i dokumentację ASEF. Autor: **Szymon Gałka**. Nie jest działającym skillem ACLT-KSeF ani ukończonym etapem jego implementacji.

Przy projektowaniu konkretnego modułu zacznij od [analizy](ANALIZA.md), a następnie otwórz wskazane pliki. Wymagania ACLT-KSeF określają [PRD](../../PRD.md), [specyfikacja](../../SPECYFIKACJA.md) i [plan](../../PLAN.md).

## Pochodzenie i zawartość

Import wykonano 2 października 2026 z dostarczonego przez autora katalogu `acli-skills/acli-ksef/versions/`. Wybrano jedną przejrzaną wersję:

```text
rel-e182a92250478d044369a64415a3008a9e5f867b440f5c54342b0cb9414da5e1
```

Wybór tej wersji nie oznacza ustalenia, że jest najnowsza. Nie kopiowano dwóch pozostałych wariantów z tej samej kolekcji.

[Snapshot](snapshot/README.md) obejmuje 74 pliki: moduły Pythona, testy, historyczny adapter OpenClaw, przykłady, schematy FA(3), dokumentację i zasoby graficzne. Pliki zachowują źródłowe nazwy `asef` i `acli-ksef`. Logo ASEF pozostaje przykładem brandingu projektu źródłowego.

[Manifest importu](import-manifest.json) podaje ścieżki, SHA-256 oryginałów i opublikowanych kopii, zmiany oraz pominięcia. Wszystkie 77 wpisów oryginalnego manifestu porównano z plikami źródłowymi przed importem. Oryginalny `release.json` zachowano jako [release.source.json](snapshot/release.source.json); opisuje bajty oryginału, więc nie jest manifestem zmodyfikowanego snapshotu ani wydania ACLT-KSeF.

## Rozdzielenie instrukcji

- `AGENTS.md` zachowano jako [AGENTS.reference.md](snapshot/AGENTS.reference.md).
- `SKILL.md` zachowano jako [SKILL.reference.md](snapshot/SKILL.reference.md).
- Oba pliki oraz źródłowy README mają informację o charakterze referencyjnym.

Treści instrukcji i konfiguracji wewnątrz snapshotu opisują projekt źródłowy. Nie należy używać ich do sterowania pracą w ACLT-KSeF, uruchamiać historycznych komend na serwerze ani traktować ich ścieżek jako konfiguracji tego projektu. Snapshot nie jest objęty instalacją docelowej paczki skilla; aktywny `SKILL.md` ACLT-KSeF powstanie w M1.

## Przegląd danych przed publikacją

Przegląd objął wybraną wersję: typy i zawartość plików, wzorce poświadczeń, kluczy prywatnych, identyfikatorów i adresów, dane przykładowej faktury oraz zależności i adresy pobierania.

Nie zidentyfikowano aktywnych poświadczeń, zapisanych kluczy prywatnych, baz użytkownika ani rzeczywistych dokumentów finansowych. W kodzie występują stałe testowe, generowanie kluczy w testach i znaczniki PEM używane przez parser; nie są to zapisane klucze. Publiczne dane kontaktowe autora pozostawiono. Przykłady stron faktury i identyfikatorów są demonstracyjne.

Numer rachunku w przykładzie JSON i jego podglądzie HTML zastąpiono **26 zerami**, aby opublikowana kopia miała jednoznacznie fikcyjny rachunek. Ta wartość nie służy do płatności ani wysyłki rzeczywistej faktury. Oryginalnej wartości nie zapisano w opisie zmian.

Pominięto cztery pliki `.pytest_cache/`. Nie importowano innych wersji, plików `.DS_Store`, środowisk uruchomieniowych, danych serwera ani jego konfiguracji. Przegląd dotyczy tego snapshotu i nie jest certyfikacją bezpieczeństwa programu.

## Autorstwo, licencje i dowody działania

Autor projektu źródłowego i ACLT-KSeF: **Szymon Gałka**. Kontakt: [kontakt@szymongalka.dev](mailto:kontakt@szymongalka.dev). Publikacja następuje w repozytorium ACLT-KSeF z licencją [GPL-3.0-only](../../LICENSE). Zasoby zewnętrzne zachowują własne warunki, w tym [informację o schematach MF](snapshot/THIRD_PARTY_NOTICES.md) i [pełną licencję MIT tych schematów](snapshot/src/asef/xsd/NOTICE.md).

Przy imporcie sprawdzono integralność plików, składnię Pythona i zakres publikowanych danych. Nie instalowano zależności ani nie uruchamiano testów funkcjonalnych lub połączeń z KSeF. Twierdzenia o wcześniejszych testach zawarte w historycznym README pozostają twierdzeniami dokumentacji źródłowej, a nie wynikiem odbioru ACLT-KSeF.
