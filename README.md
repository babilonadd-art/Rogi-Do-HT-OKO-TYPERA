# Rogi do HT — OKO TYPERA

Repozytorium obsługuje osobno:
- dotychczasowe strategie rożnych — ich kryteria i harmonogram pozostają bez zmian;
- dodatkowe typy LIVE na gole, publikowane przez `LiveOverBot.py`.

## LIVE Over 0.5 gola

- **Over 0.5 HT:** tylko wynik 0:0, minuty 1–35, kurs co najmniej 1.60.
- **Over 0.5 FT:** tylko wynik 0:0, minuty 1–75, kurs co najmniej 1.70.
- Tylko dokładna linia Over 0.5; bot nie zamienia jej na Over 1.5 ani inne rynki.
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
