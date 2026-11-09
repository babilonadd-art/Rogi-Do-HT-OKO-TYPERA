# Celne strzały FT — OKO TYPERA

Bot Telegram wyszukujący przedmeczowe typy na **celne strzały konkretnej drużyny w całym meczu (FT)** w ofercie Superbet.

## Ustawienia
- Rynek: celne strzały drużyny, pełny mecz (FT).
- Linie: Over 2.5, 3.5 lub 4.5 — wyłącznie jeśli taka linia jest rzeczywiście wystawiona.
- Kurs: od 1.70 do 2.50.
- Maksymalnie 10 typów na dzień (czas lokalny Europe/Warsaw).
- Skanowanie co 5 minut.
- Skan obejmuje pozostałą część bieżącego dnia i następny dzień.
- Ten sam mecz może zostać wysłany tylko raz; historia jest zapisywana w `celne_strzaly_ft.db`.
- Każdy typ zawiera numer, godzinę, drużynę, linię, aktualny kurs i link do meczu w Superbet.
- Przed publikacją bot ponownie sprawdza rynek i kurs. Jeśli rynek zniknie lub kurs wyjdzie poza zakres, typ nie jest wysyłany.

## Sekrety GitHub
W Settings → Secrets and variables → Actions:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Bot Telegram musi mieć uprawnienie administratora do publikowania na kanale.

## Ważne
Bot nie dopisuje kursów ani rynków, których Superbet nie udostępnia. Brak typu w danym skanie oznacza, że nie znaleziono potwierdzonej oferty spełniającej kryteria — nie jest to powód do losowego dobierania spotkań. Typy nie są gwarancją wyniku.
