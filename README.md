# Rogi Do HT OKO TYPERA

Bot Telegram uruchamiany przez darmowe GitHub Actions.

## Ustawienia
- Wyłącznie prematch: **Over 4.5 rzutów rożnych do przerwy (HT)**.
- Superbet jest źródłem terminarza i kursów.
- Open Matchday dostarcza historyczne statystyki rożnych 1. połowy.
- Bot robi szybki ranking na podstawie historii, wybiera **TOP 30 meczów**, a dopiero dla tej trzydziestki pobiera szczegółowe rynki i kursy Superbet.
- Kurs: 1.90–3.50.
- Maksymalnie 2 typy dziennie; wysyła tylko mecze, które przejdą filtr historii i mają aktywny rynek Over 4.5 HT.
- Stawka informacyjna: 50 zł.
- Automatyczne rozliczenie na podstawie statystyk 1. połowy.
- Baza SQLite zapobiega ponownemu wysłaniu tego samego meczu.

## Sekrety
W Settings → Secrets and variables → Actions dodaj:
- TELEGRAM_BOT_TOKEN — token bota z BotFather.
- TELEGRAM_CHAT_ID — ID czatu lub kanału. Bot musi mieć prawo publikowania; w kanale dodaj go jako administratora.

## Start
Actions → Rogi Do HT OKO TYPERA → Run workflow.

## Ograniczenia
Open Matchday udostępnia splity HT tylko dla obsługiwanych lig. Brak odpowiedniej historii lub aktywnego kursu oznacza brak typu — bot nie dobija do limitu na siłę. Wyniki historyczne nie gwarantują trafienia. Bot nie zawiera funkcji obstawiania zakładów.
