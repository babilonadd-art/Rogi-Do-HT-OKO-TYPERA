# Rogi Do HT OKO TYPERA

Bot Telegram monitorujący **przedmeczowe anomalie kursów HT/FT** dla piłkarskich spotkań w Superbet.

## Co monitoruje
- Skanuje spotkania przedmeczowe z najbliższych 48 godzin.
- Sprawdza jawnie opisany rynek HT/FT oraz dwuelementowe kombinacje w sekcji `superbets` (wynik 1. połowy + wynik końcowy).
- Śledzi selekcje **1/2** i **2/1**; remis do przerwy nie jest sygnałem.
- Zapisuje kolejne migawki kursów w SQLite, dzięki czemu porównuje kursy w czasie, a nie tylko pojedynczy odczyt.
- Oblicza zmianę kursu względem pierwszej zarejestrowanej wartości. Prawdopodobieństwo bez marży liczy wyłącznie wtedy, gdy dostępne jest kompletne dziewięć wyników HT/FT; przy kombinacjach niepełny rynek nie jest sztucznie normalizowany.
- Wysyła alert, gdy kurs spadnie co najmniej **15%** względem pierwszej obserwacji albo prawdopodobieństwo bez marży wzrośnie o co najmniej **5 punktów procentowych**.
- Ogranicza powtarzające się alerty: kolejny alert dla tego samego meczu i selekcji może pojawić się najwcześniej po 2 godzinach i wymaga dalszego wyraźnego ruchu.
- Skanowanie uruchamia się automatycznie co 5 minut. Historia migawek jest zapisywana w pliku `okotypera_ht.db` i commitowana przez GitHub Actions.

## Format alertu
Alert zawiera ligę, spotkanie, godzinę, rynek, selekcję 1/2 lub 2/1, kurs początkowy i aktualny, wielkość ruchu oraz link do spotkania w Superbet.

## Ważne ograniczenia
- Bot wykrywa **ruch kursów**, a nie ustawianie meczów. Zmiana może wynikać m.in. ze składów, kontuzji, informacji publicznych, płynności rynku albo zarządzania ryzykiem bukmachera.
- Monitor działa tylko wtedy, gdy Superbet udostępnia rynek HT/FT i jego kursy w danych API.
- Parser akceptuje wyłącznie jawny rynek HT/FT albo dokładnie dwie składowe kombinacji: wynik 1. połowy i wynik końcowy. Nie zgaduje selekcji z ogólnego opisu zakładu.
- Wykrycie anomalii nie jest rekomendacją postawienia zakładu ani dowodem manipulacji.
- Obecny etap monitoruje anomalie kursowe; nie publikuje typów jako pewnych zdarzeń.

## Sekrety GitHub
W Settings → Secrets and variables → Actions:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Bot Telegram musi mieć uprawnienie administratora do publikowania na kanale.
