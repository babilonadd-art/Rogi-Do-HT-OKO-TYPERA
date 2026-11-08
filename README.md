# Rogi Do HT OKO TYPERA

Bot Telegram do przedmeczowych typów **Over 9.5 rzutów rożnych — FT (cały mecz)**.

## Ustawienia
- Źródło terminarza i kursów: Superbet.
- Rynek: wyłącznie pełny mecz — Over 9.5 rzutów rożnych FT; rynki HT, 1. połowy, 2. połowy i rożne drużynowe są odrzucane.
- Akceptowany kurs: **1.70–2.60** (włącznie).
- Maksymalnie **8 typów dziennie**. To limit maksymalny, nie liczba do wypełnienia — bot może wysłać mniej lub zero typów.
- Selekcja opiera się na ostatnich meczach drużyn z Open Matchday: wymaga co najmniej 5 spotkań na drużynę, średnio minimum 3.5 rożnego dla każdej drużyny, średniej sumy co najmniej 10.5 i skuteczności Over 9.5 na poziomie co najmniej 65% w badanej próbce.
- Kandydaci są oceniani i sortowani według jakości historii; kurs jest sprawdzany bezpośrednio w aktywnym rynku Superbet, a przed wysłaniem sprawdzany ponownie. Brak odpowiednio dobrych spotkań oznacza brak typu — bot nie typuje na siłę.
- Każdy typ zaczyna się od daty meczu i godziny w strefie Europe/Warsaw.
- Stawka informacyjna: 10 zł.
- Rozliczanie FT korzysta z sumy home_corners + away_corners w Open Matchday; 10 lub więcej oznacza wygraną.
- Nowe typy FT są zapisywane w osobnej tabeli ft_picks, aby historia wcześniejszych typów HT nie blokowała nowego rynku. Oczekujące stare typy HT nadal mogą zostać rozliczone według dotychczasowych zasad.

## Dane
Open Matchday udostępnia w wynikach kolumny pełnego meczu home_corners i away_corners, a także osobne statystyki HT. Bot używa wyłącznie kolumn FT do selekcji i rozliczeń nowego rynku.

## Sekrety
W Settings → Secrets and variables → Actions:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID

Bot musi mieć uprawnienie administratora do publikowania w kanale.

## Uruchamianie
Workflow skanuje co 5 minut mecze przedmeczowe z bieżącego dnia. Można go też uruchomić ręcznie w GitHub Actions.

## Ograniczenia
Dane Open Matchday są dostępne tylko dla obsługiwanych lig i spotkań, dla których istnieje historia. Brak próbki, brak aktywnego rynku lub kurs poza zakresem oznacza brak typu. Statystyki historyczne nie gwarantują wyniku. Bot nie zawiera funkcji obstawiania zakładów.
