# Rogi Do HT OKO TYPERA

Bot Telegram uruchamiany przez darmowe GitHub Actions.

## Ustawienia
- Tylko prematch: Over 4.5 rzutów rożnych do przerwy.
- Mecz co najmniej 3 godziny od skanowania; okno do 48 godzin.
- Maksymalnie 2 typy dziennie.
- Stawka informacyjna: 50 zł.
- Automatyczne rozliczenie po zakończeniu meczu na podstawie statystyk 1. połowy.
- Baza SQLite zapobiega ponownemu wysłaniu tego samego meczu.
- Skan co 30 minut.

## Sekrety
W Settings → Secrets and variables → Actions dodaj:
- TELEGRAM_BOT_TOKEN — token bota z BotFather.
- TELEGRAM_CHAT_ID — ID czatu lub kanału. Bot musi mieć prawo publikowania; w kanale dodaj go jako administratora.

## Start
Actions → Rogi Do HT OKO TYPERA → Run workflow.

## Ograniczenia
Dane pochodzą z nieoficjalnego publicznego API SofaScore, które może zmieniać format lub ograniczać zapytania. Jeśli SofaScore nie udostępni statystyk 1. połowy, typ nie zostanie wybrany albo rozliczenie poczeka na kolejne uruchomienie. Bot nie wysyła typów na siłę. Wyniki historyczne nie gwarantują trafienia. Bot nie zawiera funkcji obstawiania zakładów.
