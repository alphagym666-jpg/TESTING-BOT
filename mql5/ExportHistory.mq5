//+------------------------------------------------------------------+
//| ExportHistory.mq5 - exporte l'historique du graphique en CSV      |
//| Pour utiliser la plateforme sans le package Python (Mac, VPS...) |
//| Installation : copier dans MQL5\Scripts, compiler (F7),          |
//| puis glisser le script sur un graphique.                         |
//| Le fichier est créé dans MQL5\Files\<SYMBOLE>_<TF>.csv           |
//+------------------------------------------------------------------+
#property script_show_inputs
input int InpBars = 60000; // nombre de bougies à exporter

void OnStart()
  {
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int n = CopyRates(_Symbol, _Period, 0, InpBars, r);
   if(n <= 0)
     {
      Print("Erreur CopyRates : ", GetLastError());
      return;
     }
   string tf = StringSubstr(EnumToString(_Period), 7);
   string name = _Symbol + "_" + tf + ".csv";
   int h = FileOpen(name, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(h == INVALID_HANDLE)
     {
      Print("Impossible de créer ", name, " : ", GetLastError());
      return;
     }
   FileWrite(h, "time", "open", "high", "low", "close", "volume", "spread");
   MqlDateTime t;
   for(int i = 0; i < n; i++)
     {
      TimeToStruct(r[i].time, t);
      FileWrite(h, StringFormat("%04d-%02d-%02d %02d:%02d", t.year, t.mon, t.day, t.hour, t.min),
                DoubleToString(r[i].open, _Digits), DoubleToString(r[i].high, _Digits),
                DoubleToString(r[i].low, _Digits), DoubleToString(r[i].close, _Digits),
                (string)r[i].tick_volume, (string)r[i].spread);
     }
   FileClose(h);
   Print("Exporté ", n, " bougies dans MQL5\\Files\\", name);
  }
