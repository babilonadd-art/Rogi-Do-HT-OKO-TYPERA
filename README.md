# Rogi do HT — OKO TYPERA

Repozytorium obsługuje osobno:
- dotychczasowe strategie rożnych — ich kryteria i harmonogram pozostają bez zmian;
- dodatkowe typy LIVE na gole, publikowane przez `LiveOverBot.py`.

## LIVE Over 0.5 i Over 1.5 gola

- **Over 0.5 HT i FT:** typ dostępny tylko przed pierwszym golem w odpowiednim okresie.
- **Over 1.5 HT i FT:** typ dostępny, dopóki w odpowiednim okresie nie padły 2 gole.
- **HT:** minuty 1–35, kurs co najmniej 1.60.
- **FT:** minuty 1–75, kurs co najmniej 1.70.
- Bot wybiera wyłącznie dokładne linie Over 0.5 lub Over 1.5, bez podmiany na inne rynki.
- Kurs i rynek są sprawdzane ponownie bezpośrednio przed wysłaniem.
- Maksymalnie **10 typów łącznie dziennie** dla HT i FT (strefa Europe/Warsaw).
- Skan LIVE uruchamia się co 5 minut.
- Źródło kursów i listy spotkań: oferta LIVE Superbet. Nie używamy Flashscore ani SofaScore.
- Historia wysłanych typów LIVE jest zapisywana w `okotypera_rogi.db`, co blokuje duplikaty.

## Telegram

W GitHub → Settings → Secrets and variables → Actions wymagane są:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Bot Telegram musi mieć uprawnienie administratora do publikowania na kanale.

## Ważne

Bot publikuje tylko rzeczywisty, aktywny rynek i kurs od wskazanego minimum. Jeśli Superbet nie udostępnia odpowiedniego rynku albo wynik nie wynosi 0:0, typ nie zostanie wysłany. Brak typu nie jest powodem do dobierania meczu na siłę. Typy nie gwarantują wygranej.
