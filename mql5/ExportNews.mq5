//+------------------------------------------------------------------+
//| ExportNews.mq5 - exporte le calendrier économique MT5 en CSV     |
//| (annonces à fort impact : NFP, CPI, FOMC, BCE...)                 |
//| Installation : copier dans MQL5\Scripts, compiler (F7),          |
//| puis glisser le script sur n'importe quel graphique.             |
//| Le fichier est créé dans le dossier COMMUN des terminaux :        |
//|   %APPDATA%\MetaQuotes\Terminal\Common\Files\news.csv            |
//| La plateforme Python le trouve toute seule à cet endroit.        |
//| Les heures sont en heure du SERVEUR (comme les bougies).         |
//+------------------------------------------------------------------+
#property script_show_inputs
input int  InpYears      = 6;    // années d'historique
input int  InpDaysAhead  = 30;   // jours à venir (pour le paper trading)
input bool InpMediumToo  = false; // inclure aussi l'impact moyen

void OnStart()
  {
   string currencies[] = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"};
   datetime from = TimeTradeServer() - (datetime)InpYears * 365 * 86400;
   datetime to   = TimeTradeServer() + (datetime)InpDaysAhead * 86400;
   int h = FileOpen("news.csv", FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_COMMON, ',');
   if(h == INVALID_HANDLE)
     {
      Print("Impossible de créer news.csv : ", GetLastError());
      return;
     }
   FileWrite(h, "time", "currency", "importance", "event");
   int total = 0;
   for(int c = 0; c < ArraySize(currencies); c++)
     {
      MqlCalendarValue values[];
      int n = CalendarValueHistory(values, from, to, NULL, currencies[c]);
      for(int i = 0; i < n; i++)
        {
         MqlCalendarEvent ev;
         if(!CalendarEventById(values[i].event_id, ev))
            continue;
         int imp = (int)ev.importance; // 0 aucune, 1 faible, 2 moyenne, 3 forte
         if(imp < 3 && !(InpMediumToo && imp == 2))
            continue;
         MqlDateTime t;
         TimeToStruct(values[i].time, t);
         string name = ev.name;
         StringReplace(name, ",", " ");
         FileWrite(h, StringFormat("%04d-%02d-%02d %02d:%02d", t.year, t.mon, t.day, t.hour, t.min),
                   currencies[c], (string)imp, name);
         total++;
        }
     }
   FileClose(h);
   Print("Exporté ", total, " annonces dans Common\\Files\\news.csv");
  }
