# Rogi Do HT OKO TYPERA

Bot Telegram monitorujący **przedmeczowe anomalie kursów HT/FT** dla piłkarskich spotkań w Superbet.

## Co monitoruje
- Skanuje spotkania przedmeczowe z najbliższych 48 godzin.
- Sprawdza wyłącznie jawnie opisany rynek HT/FT (do przerwy / wynik końcowy).
- Śledzi selekcje **1/2** i **2/1**; remis do przerwy nie jest sygnałem.
- Zapisuje kolejne migawki kursów w SQLite, dzięki czemu porównuje kursy w czasie, a nie tylko pojedynczy odczyt.
- Oblicza zmianę kursu względem pierwszej zarejestrowanej wartości oraz zmianę prawdopodobieństwa implikowanego po normalizacji marży na podstawie dostępnych wyników rynku.
- Wysyła alert, gdy kurs spadnie co najmniej **15%** względem pierwszej obserwacji albo prawdopodobieństwo bez marży wzrośnie o co najmniej **5 punktów procentowych**.
- Ogranicza powtarzające się alerty: kolejny alert dla tego samego meczu i selekcji może pojawić się najwcześniej po 2 godzinach i wymaga dalszego wyraźnego ruchu.
- Skanowanie uruchamia się automatycznie co 5 minut. Historia migawek jest zapisywana w pliku `okotypera_ht.db` i commitowana przez GitHub Actions.

## Format alertu
Alert zawiera ligę, spotkanie, godzinę, rynek, selekcję 1/2 lub 2/1, kurs początkowy i aktualny, wielkość ruchu oraz link do spotkania w Superbet.

## Ważne ograniczenia
- Bot wykrywa **ruch kursów**, a nie ustawianie meczów. Zmiana może wynikać m.in. ze składów, kontuzji, informacji publicznych, płynności rynku albo zarządzania ryzykiem bukmachera.
- Monitor działa tylko wtedy, gdy Superbet udostępnia rynek HT/FT i jego kursy w danych API.
- Parser akceptuje wyłącznie rynek jawnie nazwany jako HT/FT i selekcje 1/2 lub 2/1. Jeśli nazwa rynku lub etykiety wyników różnią się od obsługiwanych, bot pominie rynek zamiast zgadywać.
- Wykrycie anomalii nie jest rekomendacją postawienia zakładu ani dowodem manipulacji.
- Obecny etap monitoruje anomalie kursowe; nie publikuje typów jako pewnych zdarzeń.

## Sekrety GitHub
W Settings → Secrets and variables → Actions:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Bot Telegram musi mieć uprawnienie administratora do publikowania na kanale.
