# ACLI-KSeF

Skill dla agentów AI z własnym CLI w Pythonie do obsługi Krajowego Systemu e-Faktur. Projekt tworzy **Szymon Gałka**. Docelowe środowisko to serwer Linux, głównie z OpenClaw.

Priorytetem jest szybka obsługa przez agenta: krótka instrukcja, łatwe odkrywanie funkcji, kompletne zadania wykonywane przez CLI i przewidywalne wyniki JSON.

## Stan projektu

Projekt jest w fazie planowania. PRD, specyfikacja i plan mają wersję **0.4** i status „projekt do przeglądu”. Repozytorium zawiera dokumentację; skill i CLI pozostają do zaimplementowania.

## Dokumentacja

- [PRD](PRD.md) — cele produktu, zakres i kryteria odbioru.
- [Specyfikacja techniczna](SPECYFIKACJA.md) — architektura, kontrakt CLI, dane i integracja KSeF.
- [Plan realizacji](PLAN.md) — etapy M0–M6, zależności i wymagane dowody wykonania.

## Zakres docelowy

- Pobieranie, synchronizacja i wyszukiwanie faktur.
- Odczyt oryginalnych XML i podglądy HTML oraz PDF.
- Przygotowanie, walidacja i wysyłka faktur oraz korekt po zatwierdzeniu dokładnej wersji.
- Sprawdzanie statusu KSeF, zachowanie numeru KSeF i UPO.
- Historia operacji, wznowienie po przerwaniu i kopia danych.

Funkcje powstają etapami. Pierwsza wersja użytkowa obejmie pobieranie i przeglądanie dokumentów, a kolejne dodadzą wystawianie, wysyłkę i korekty.

## Nazwa i autorstwo

Nazwa produktu to **ACLI-KSeF**, a identyfikator skilla, paczki i CLI to `acli-ksef`. CLI będzie częścią paczki skilla. Dokumentacja wskazuje autora projektu; ta informacja znajdzie się również w skillu, pomocy CLI oraz podglądach.

**Autor i twórca projektu: Szymon Gałka.**

© 2026 Szymon Gałka.

## Licencja

Kod i dokumentacja ACLI-KSeF są udostępniane na licencji **GNU General Public License, wersja 3 (GPLv3)**. Identyfikator SPDX: `GPL-3.0-only`. Pełny tekst licencji znajduje się w pliku [LICENSE](LICENSE).

Zależności i zewnętrzne zasoby zachowują własne informacje o autorach i licencjach.
