# Rogi Do HT OKO TYPERA

Bot Telegram do przedmeczowego rynku **Over 4.5 rzutów rożnych do przerwy (HT)**.

## Ustawienia
- Źródło terminarza i kursów: Superbet.
- Rynek: wyłącznie Over 4.5 rożnych HT.
- Akceptowany kurs: **1.70–2.60** (włącznie); kursy poza zakresem są odrzucane.
- Maksymalnie **2 typy dziennie**.
- Bot ocenia historię rożnych HT obu drużyn z Open Matchday, wymaga minimalnej próbki oraz filtruje mecze według średnich i odsetka spotkań z co najmniej 5 rożnymi do przerwy.
- Mecze są sortowane według wyniku jakości; kursy pobierane są dla najwyżej ocenionych spotkań. Bot nie dobija do limitu na siłę.
- Stawka informacyjna: 10 zł.
- Typy są rozliczane na podstawie sumy rożnych w pierwszej połowie z Open Matchday; minimum 5 oznacza wygraną.
- Osobna baza `okotypera_ht.db` zapobiega ponownemu wysłaniu i nie koliduje z bazą bota LIVE.

## Dane Bet365
Bet365 nie udostępnia publicznego, oficjalnego API. Jego Match Insights są prezentowane w serwisie, ale nie stanowią publicznego źródła danych dla tego bota, a oficjalna lista przedmeczowych statystyk drużyn nie obejmuje historii rzutów rożnych HT. Dlatego do selekcji rożnych wykorzystywane są dane Open Matchday zamiast niepewnego scrapowania strony lub płatnego pośrednika.

## Sekrety
W Settings → Secrets and variables → Actions:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Bot musi mieć uprawnienie administratora do publikowania w kanale.

## Uruchamianie
Workflow `Rogi HT Prematch` uruchamia skan co 5 minut dla pozostałych meczów danego dnia. Można go też uruchomić ręcznie w GitHub Actions.

## Ograniczenia
Open Matchday udostępnia statystyki HT tylko dla obsługiwanych lig i spotkań, w których dane są dostępne. Brak próbki, brak aktywnego rynku lub kurs poza zakresem oznacza brak typu. Wyniki historyczne nie gwarantują trafienia. Bot nie zawiera funkcji obstawiania zakładów.
