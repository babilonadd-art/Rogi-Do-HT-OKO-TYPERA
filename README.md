# Rogi FT — OKO TYPERA

Aktywny moduł `RogiFTDuble.py` publikuje wyłącznie przedmeczowe duble na rzuty rożne FT (cały mecz).

## Zasady typowania
- Wyłącznie rynek rożnych FT — bez HT i bez LIVE.
- Wyłącznie Overy; żadne Undery nie są publikowane.
- Każde zdarzenie w dubelu musi mieć kurs co najmniej **1.80**.
- Jeden dubel = dokładnie 2 zdarzenia.
- Maksymalnie **2 duble dziennie** (4 pojedyncze zdarzenia).
- Skanowanie co 5 minut przez GitHub Actions.
- Mecz musi rozpoczynać się co najmniej 3 godziny po skanowaniu.
- Nie są używane statystyki ani historia drużyn do wyboru zdarzeń.
- Bot nie publikuje ponownie tego samego meczu.
- Stawka rozliczeniowa: 50 zł za dubel.
- Automatyczne rozliczanie po meczu i dzienne podsumowanie zysku/straty.

## Źródła i ograniczenia
- Lista meczów, rynek FT Over i kurs są pobierane z publicznej oferty Superbet.
- Wynik rożnych do rozliczenia jest sprawdzany w Cornerflick, a awaryjnie w Open Matchday.
- FootyBets został na razie sprawdzony diagnostycznie, ale nie jest aktywnym źródłem produkcyjnym: test nie potwierdził jeszcze stabilnego, jednoznacznego odczytu wszystkich pól potrzebnych do bezpiecznego wysyłania dubli.
- Publiczna dostępność strony nie oznacza automatycznie zgody na masowe pobieranie i dalsze publikowanie jej prognoz.

## Konfiguracja Telegrama
W GitHub → Settings → Secrets and variables → Actions wymagane są:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Bot musi mieć uprawnienie do publikowania na kanale.

## Uruchamianie
Workflow `.github/workflows/Rogi-FT-Duble.yml` uruchamia skan co 5 minut. Historia dubli jest przechowywana w `rogift_duble.db` i zapisywana w repozytorium po wykonaniu skanu.

Brak dubla oznacza, że nie znaleziono dwóch zdarzeń spełniających warunki — bot nie dobiera meczów na siłę. Kursy mogą się zmieniać, a typy nie gwarantują wygranej.
