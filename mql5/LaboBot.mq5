//+------------------------------------------------------------------+
//| LaboBot.mq5 - bot MT5 de la stratégie combinée du Directeur      |
//|                                                                  |
//| Le cerveau reste dans la plateforme Python (le même code que     |
//| celui qui a été testé). Le paper trading de la stratégie         |
//| combinée écrit ses décisions dans Common\Files\labo_signaux.csv ; |
//| ce bot les lit chaque seconde et passe les ordres sur le compte : |
//|   OPEN  : ouvre au marché avec SL et TP, lot calculé pour perdre  |
//|           au maximum risque_pct % du capital au stop              |
//|   MOVE  : déplace le stop (stop suiveur)                          |
//|   BE    : stop au prix d'entrée (break-even)                      |
//|   CLOSE : ferme la position (signal opposé, durée max...)         |
//|                                                                  |
//| Garde-fous propres au bot (même si Python se trompe) :           |
//|  - refuse un compte RÉEL sauf si InpAutoriserReel = true         |
//|  - risque par trade plafonné (InpRisqueMax)                      |
//|  - perte du jour >= InpPerteJourMax % : tout est fermé, plus de  |
//|    trade jusqu'au lendemain                                       |
//|  - perte totale >= InpPerteTotaleMax % : tout est fermé, arrêt   |
//|  - objectif atteint (InpObjectif %) : plus de nouveau trade      |
//|  - signal trop vieux (PC en veille, redémarrage) : ignoré        |
//|                                                                  |
//| Installation : voir LISEZMOI_BOT.txt                              |
//+------------------------------------------------------------------+
#property copyright "Labo de stratégies MT5"
#property version   "1.00"

#include <Trade\Trade.mqh>

input double InpCapital         = 100000; // capital de départ du compte (base des % de perte)
input double InpRisqueMax       = 1.0;    // risque max par trade en % (plafond, même si le signal demande plus)
input double InpPerteJourMax    = 2.8;    // perte du jour qui déclenche la fermeture de tout (%)
input double InpPerteTotaleMax  = 9.5;    // perte totale qui arrête le bot (%)
input double InpObjectif        = 10.0;   // objectif du challenge en % (0 = pas d'arrêt à l'objectif)
input int    InpDelaiMaxSec     = 90;     // un signal d'ouverture plus vieux que ça est ignoré
input long   InpMagic           = 260926; // numéro magique des ordres du bot
input bool   InpAutoriserReel   = false;  // autoriser un compte RÉEL (laisser false pour un challenge / démo)
input string InpFichier         = "labo_signaux.csv"; // fichier des signaux (dossier commun de MT5)

CTrade   trade;
long     g_last_seq = 0;
string   g_gv_seq, g_gv_stop;
datetime g_day = 0;
double   g_day_start = 0;
bool     g_block_day = false;
datetime g_last_signal = 0;
string   g_status = "";

//+------------------------------------------------------------------+
int OnInit()
  {
   long mode = AccountInfoInteger(ACCOUNT_TRADE_MODE);
   if(mode == ACCOUNT_TRADE_MODE_REAL && !InpAutoriserReel)
     {
      Alert("LaboBot : compte RÉEL détecté. Le bot refuse de trader (mettre InpAutoriserReel = true pour forcer).");
      return(INIT_FAILED);
     }
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      Alert("LaboBot : activez le bouton « Algo Trading » en haut de MT5, sinon aucun ordre ne partira.");
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(30);
   g_gv_seq  = "LaboBot_seq_" + IntegerToString(InpMagic);
   g_gv_stop = "LaboBot_arret_" + IntegerToString(InpMagic);
   // au premier lancement, on ne rejoue pas les anciens signaux du fichier
   if(GlobalVariableCheck(g_gv_seq))
      g_last_seq = (long)GlobalVariableGet(g_gv_seq);
   else
     {
      g_last_seq = (long)TimeGMT() * 1000;
      GlobalVariableSet(g_gv_seq, (double)g_last_seq);
     }
   NewDay();
   EventSetTimer(1);
   Print("LaboBot démarré : capital ", InpCapital, ", risque max ", InpRisqueMax, " %/trade, perte max ",
         InpPerteJourMax, " %/jour et ", InpPerteTotaleMax, " % au total.");
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   Comment("");
  }

void OnTimer()
  {
   Guards();
   ReadSignals();
   ShowStatus();
  }

//+------------------------------------------------------------------+
//| Garde-fous                                                        |
//+------------------------------------------------------------------+
void NewDay()
  {
   MqlDateTime t;
   TimeToStruct(TimeCurrent(), t);
   t.hour = 0; t.min = 0; t.sec = 0;
   datetime d = StructToTime(t);
   if(d != g_day)
     {
      g_day = d;
      g_day_start = AccountInfoDouble(ACCOUNT_BALANCE);  // comme FTMO : solde au début de la journée
      g_block_day = false;
     }
  }

bool Stopped() { return GlobalVariableCheck(g_gv_stop) && GlobalVariableGet(g_gv_stop) > 0; }

void Guards()
  {
   NewDay();
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double day_loss = (g_day_start - eq) / InpCapital * 100.0;
   double total_loss = (InpCapital - eq) / InpCapital * 100.0;
   if(!Stopped() && total_loss >= InpPerteTotaleMax)
     {
      CloseAll("perte totale " + DoubleToString(total_loss, 2) + " %");
      GlobalVariableSet(g_gv_stop, 1);
      Alert("LaboBot ARRÊTÉ : perte totale ", DoubleToString(total_loss, 2), " %. Supprimez la variable globale ",
            g_gv_stop, " (F3) pour le relancer.");
     }
   if(!g_block_day && day_loss >= InpPerteJourMax)
     {
      CloseAll("perte du jour " + DoubleToString(day_loss, 2) + " %");
      g_block_day = true;
      Alert("LaboBot : perte du jour ", DoubleToString(day_loss, 2), " % -> tout est fermé, reprise demain.");
     }
  }

bool TargetReached()
  {
   if(InpObjectif <= 0)
      return false;
   return (AccountInfoDouble(ACCOUNT_BALANCE) - InpCapital) / InpCapital * 100.0 >= InpObjectif;
  }

void CloseAll(string why)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionGetInteger(POSITION_MAGIC) == InpMagic)
         trade.PositionClose(ticket);
     }
   Print("LaboBot : fermeture de toutes les positions (", why, ")");
  }

//+------------------------------------------------------------------+
//| Lecture des signaux                                               |
//+------------------------------------------------------------------+
void ReadSignals()
  {
   int h = FileOpen(InpFichier, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE)
      return;
   string lines[];
   int n = 0;
   while(!FileIsEnding(h))
     {
      string line = FileReadString(h);
      if(StringLen(line) > 0)
        {
         ArrayResize(lines, n + 1);
         lines[n++] = line;
        }
     }
   FileClose(h);
   for(int i = 0; i < n; i++)
     {
      string f[];
      if(StringSplit(lines[i], ';', f) < 11)
         continue;
      long seq = StringToInteger(f[0]);
      if(seq <= g_last_seq)
         continue;                       // en-tête (seq = 0) ou déjà traité
      Execute(f);
      g_last_seq = seq;
      GlobalVariableSet(g_gv_seq, (double)g_last_seq);
     }
  }

ulong FindPosition(string symbol, string key)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionGetInteger(POSITION_MAGIC) == InpMagic && PositionGetString(POSITION_SYMBOL) == symbol
         && PositionGetString(POSITION_COMMENT) == "LB" + key)
         return ticket;
     }
   return 0;
  }

double Lots(string symbol, double risk_money, double dist, double commission)
  {
   double tick_value = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   if(tick_value <= 0 || tick_size <= 0 || step <= 0)
      return 0;
   double per_lot = dist / tick_size * tick_value + commission;   // perte au stop pour 1 lot
   if(per_lot <= 0)
      return 0;
   double lots = MathFloor(risk_money / per_lot / step + 1e-9) * step;
   lots = MathMin(lots, vmax);
   return lots >= vmin ? NormalizeDouble(lots, 8) : 0;
  }

void Execute(string &f[])
  {
   string action = f[2], key = f[3], symbol = f[4];
   int    side = (int)StringToInteger(f[5]);
   double dist = StringToDouble(f[6]), rr = StringToDouble(f[7]), risk = StringToDouble(f[8]);
   double price = StringToDouble(f[9]), commission = StringToDouble(f[10]);
   long   age = (long)TimeGMT() - StringToInteger(f[1]);
   g_last_signal = TimeCurrent();
   if(!SymbolSelect(symbol, true))
     {
      Print("LaboBot : symbole inconnu ", symbol);
      return;
     }
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   ulong ticket = FindPosition(symbol, key);

   if(action == "OPEN")
     {
      if(Stopped() || g_block_day)       { Print("LaboBot : signal ignoré (garde-fou actif) ", symbol); return; }
      if(TargetReached())                { Print("LaboBot : objectif atteint, plus de nouveau trade"); return; }
      if(age > InpDelaiMaxSec)           { Print("LaboBot : signal trop vieux (", age, " s) ignoré ", symbol); return; }
      if(ticket > 0 || dist <= 0 || side == 0)
         return;
      double stops = SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL) * SymbolInfoDouble(symbol, SYMBOL_POINT);
      if(dist <= stops)                  { Print("LaboBot : stop trop proche pour ", symbol); return; }
      double risk_money = InpCapital * MathMin(risk, InpRisqueMax) / 100.0;
      double lots = Lots(symbol, risk_money, dist, commission);
      if(lots <= 0)                      { Print("LaboBot : lot trop petit pour le risque demandé ", symbol); return; }
      MqlTick tk;
      if(!SymbolInfoTick(symbol, tk))
         return;
      double entry = side > 0 ? tk.ask : tk.bid;
      double sl = NormalizeDouble(entry - side * dist, digits);
      double tp = rr > 0 ? NormalizeDouble(entry + side * rr * dist, digits) : 0;
      trade.SetTypeFillingBySymbol(symbol);
      bool ok = side > 0 ? trade.Buy(lots, symbol, 0, sl, tp, "LB" + key)
                         : trade.Sell(lots, symbol, 0, sl, tp, "LB" + key);
      Print("LaboBot : ", side > 0 ? "ACHAT " : "VENTE ", lots, " ", symbol, " SL ", sl, " TP ", tp, " -> ",
            ok ? "OK" : "ÉCHEC", " (", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription(), ")");
      return;
     }
   if(ticket == 0 || !PositionSelectByTicket(ticket))
      return;                            // déjà fermée chez le courtier (SL / TP touché)
   long   type = PositionGetInteger(POSITION_TYPE);
   double cur_sl = PositionGetDouble(POSITION_SL), cur_tp = PositionGetDouble(POSITION_TP);
   double open_px = PositionGetDouble(POSITION_PRICE_OPEN);
   if(action == "CLOSE")
     {
      trade.PositionClose(ticket);
      Print("LaboBot : fermeture ", symbol, " (signal de la stratégie)");
     }
   else if(action == "MOVE" || action == "BE")
     {
      double new_sl = NormalizeDouble(action == "BE" ? open_px : price, digits);
      bool better = type == POSITION_TYPE_BUY ? new_sl > cur_sl : (cur_sl == 0 || new_sl < cur_sl);
      if(better)
         trade.PositionModify(ticket, new_sl, cur_tp);
     }
  }

//+------------------------------------------------------------------+
void ShowStatus()
  {
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t > 0 && PositionGetInteger(POSITION_MAGIC) == InpMagic)
         n++;
     }
   string state = Stopped() ? "ARRÊTÉ (perte totale)" : g_block_day ? "en pause jusqu'à demain (perte du jour)"
                  : TargetReached() ? "objectif atteint" : "actif";
   Comment("LaboBot - ", state,
           "\nJour : ", DoubleToString((eq - g_day_start) / InpCapital * 100.0, 2), " %   (arrêt à -",
           DoubleToString(InpPerteJourMax, 1), " %)",
           "\nTotal : ", DoubleToString((eq - InpCapital) / InpCapital * 100.0, 2), " %   (arrêt à -",
           DoubleToString(InpPerteTotaleMax, 1), " %)",
           "\nPositions du bot : ", n,
           "\nDernier signal reçu : ", g_last_signal > 0 ? TimeToString(g_last_signal) : "aucun",
           "\nLe paper trading de la stratégie combinée (option C) doit tourner pour envoyer les signaux.");
  }
