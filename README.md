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
- Nowe duble są wysyłane jako dynamiczna grafika PNG w stylu OKO TYPERA; grafika zawiera oba mecze, linie FT, kursy, podatek 12%, kwotę grającą, kurs łączny, wypłatę i zysk netto.
- Każda publikacja otrzymuje unikalny identyfikator `OT-...`, zapisany razem z kuponem w bazie i powtarzany w ramce grafiki oraz podpisie Telegrama.
- Ramka ochronna znajduje się poza oryginalną grafiką — nie zasłania nazw drużyn, linii ani typów; test sprawdza zgodność pikseli oryginału.
- Zdjęcia i wiadomości są wysyłane z `protect_content=True`; ogranicza to natywne przekazywanie i zapisywanie w Telegramie, ale nie blokuje zrzutów ekranu ani fotografowania ekranu.
- Każdy dubel otrzymuje podpis HMAC-SHA256 obejmujący identyfikator kuponu, ligę, drużyny, godzinę meczu, rynek, linię, kursy i stawkę; zmiana danych powoduje inną sygnaturę.
- W ramce znajduje się kod QR prowadzący do dokładnego oryginalnego posta w oficjalnym kanale, a podpis i link są również w opisie Telegrama. Przy ponownej publikacji zachowywany jest identyfikator, podpis i odnośnik do pierwszego oryginału.
- Klucz podpisu pobierany jest z `PUBLICATION_SIGNING_SECRET`; jeśli ten sekret nie jest ustawiony, bot używa tokena Telegrama jako klucza zastępczego. Nie umieszczaj żadnego z tych kluczy w kodzie ani publicznym repozytorium.
- Każdy dubel otrzymuje podpis HMAC-SHA256 obejmujący identyfikator kuponu, ligę, drużyny, godzinę meczu, rynek, linię, kursy i stawkę; zmiana danych powoduje inną sygnaturę.
- W ramce znajduje się kod QR prowadzący do dokładnego oryginalnego posta w oficjalnym kanale, a podpis i link są również w opisie Telegrama. Przy ponownej publikacji zachowywany jest identyfikator, podpis i odnośnik do pierwszego oryginału.
- Klucz podpisu pobierany jest z `PUBLICATION_SIGNING_SECRET`; jeśli ten sekret nie jest ustawiony, bot bezpiecznie używa tokena Telegrama jako klucza zastępczego. Nie umieszczaj żadnego z tych kluczy w kodzie ani publicznym repozytorium.
- Automatyczne rozliczanie po meczu i dzienne podsumowanie zysku/straty.

## Źródła i ograniczenia
- Lista meczów, rynek FT Over i kurs są pobierane z publicznej oferty Superbet. Filtr piłki nożnej korzysta z identyfikatora sportu 5.
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
