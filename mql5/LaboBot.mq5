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
input double InpMeilleurJour    = 50.0;   // règle du meilleur jour : une journée <= X % du profit total (0 = aucune)
input bool   InpPerteSuiveuse   = true;   // FTMO 1 étape : perte max SUIVEUSE (plus haut solde de fin de journée - X %)
input bool   InpComposer        = false;
input int    InpFermerVendredi  = 0;      // compte Standard financé : heure serveur du vendredi où tout est fermé (0 = non)  // true = risque calculé sur le SOLDE (compte perso, intérêts composés)
input int    InpDelaiMaxSec     = 90;     // un signal d'ouverture plus vieux que ça est ignoré
input long   InpMagic           = 260926; // numéro magique des ordres du bot
input bool   InpAutoriserReel   = false;  // autoriser un compte RÉEL (laisser false pour un challenge / démo)
input string InpFichier         = "labo_signaux.csv"; // fichier des signaux (dossier commun de MT5)

CTrade   trade;
long     g_last_seq = 0;
string   g_gv_seq, g_gv_stop;
datetime g_day = 0;
double   g_day_start = 0;
string   g_gv_best;
string   g_gv_eod;   // plus haut solde de fin de journée (perte max suiveuse), gardé si MT5 redémarre

double BestDay() { return GlobalVariableCheck(g_gv_best) ? GlobalVariableGet(g_gv_best) : 0.0; }
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
   g_gv_best = "LaboBot_meilleurjour_" + IntegerToString(InpMagic);
   g_gv_eod = "LaboBot_plushautsolde_" + IntegerToString(InpMagic);
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
   static datetime last_ping = 0;
   Guards();
   ReadSignals();
   ShowStatus();
   if(TimeLocal() - last_ping >= 60)   // signe de vie pour le surveillant de la plateforme
     {
      last_ping = TimeLocal();
      LogExec("PING", "-", "", 0, 0, true, "");
     }
  }

// journal d'exécution lu par le surveillant (glissement, ordres manqués, bot arrêté)
void LogExec(string action, string key, string symbol, double price, double lots, bool ok, string msg)
  {
   int h = FileOpen("exec_" + InpFichier, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON |
                    FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE)
      return;
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, IntegerToString((long)TimeGMT()) + ";" + action + ";" + key + ";" + symbol + ";" +
                   DoubleToString(price, 8) + ";" + DoubleToString(lots, 2) + ";" + (ok ? "1" : "0") + ";" + msg + "\r\n");
   FileClose(h);
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
      if(g_day != 0)  // meilleure journée (argent), gardée même si MT5 redémarre
        {
         double prev = AccountInfoDouble(ACCOUNT_BALANCE) - g_day_start;
         if(prev > BestDay())
            GlobalVariableSet(g_gv_best, prev);
         if(AccountInfoDouble(ACCOUNT_BALANCE) > EodHigh())  // solde de clôture de la veille
            GlobalVariableSet(g_gv_eod, AccountInfoDouble(ACCOUNT_BALANCE));
        }
      g_day = d;
      g_day_start = AccountInfoDouble(ACCOUNT_BALANCE);  // comme FTMO : solde au début de la journée
      g_block_day = false;
     }
  }

double EodHigh() { return GlobalVariableCheck(g_gv_eod) ? GlobalVariableGet(g_gv_eod) : InpCapital; }

// plancher de la perte max totale : fixe (capital - X %) ou suiveux (plus haut solde de fin de journée - X % du
// capital, jamais au-dessus du capital de départ), comme FTMO 1 étape
double Floor()
  {
   double cut = InpCapital * InpPerteTotaleMax / 100.0;
   if(!InpPerteSuiveuse)
      return InpCapital - cut;
   return MathMin(MathMax(InpCapital, EodHigh()) - cut, InpCapital);
  }

bool Stopped() { return GlobalVariableCheck(g_gv_stop) && GlobalVariableGet(g_gv_stop) > 0; }

void Guards()
  {
   NewDay();
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double day_loss = (g_day_start - eq) / InpCapital * 100.0;
   double total_loss = (InpCapital - eq) / InpCapital * 100.0;
   if(!Stopped() && eq <= Floor())
     {
      CloseAll("perte totale " + DoubleToString(total_loss, 2) + " %");
      GlobalVariableSet(g_gv_stop, 1);
      Alert("LaboBot ARRÊTÉ : perte totale ", DoubleToString(total_loss, 2), " %. Supprimez la variable globale ",
            g_gv_stop, " (F3) pour le relancer.");
     }
   MqlDateTime now;
   TimeToStruct(TimeCurrent(), now);
   if(InpFermerVendredi > 0 && now.day_of_week == 5 && now.hour >= InpFermerVendredi && !g_block_day)
     {  // pas de position pendant le week-end (sécurité, même si la plateforme Python est arrêtée)
      CloseAll("fermeture avant le week-end");
      g_block_day = true;
     }
   if(!g_block_day && day_loss >= InpPerteJourMax)
     {
      CloseAll("perte du jour " + DoubleToString(day_loss, 2) + " %");
      g_block_day = true;
      Alert("LaboBot : perte du jour ", DoubleToString(day_loss, 2), " % -> tout est fermé, reprise demain.");
     }
  }

// objectif réel : +InpObjectif %, ou plus si la meilleure journée dépasse InpMeilleurJour % du profit total
double TargetNeeded()
  {
   double best = MathMax(BestDay(), AccountInfoDouble(ACCOUNT_BALANCE) - g_day_start) / InpCapital * 100.0;
   if(InpMeilleurJour > 0 && best > 0)
      return MathMax(InpObjectif, best / (InpMeilleurJour / 100.0));
   return InpObjectif;
  }

bool TargetReached()
  {
   if(InpObjectif <= 0)
      return false;
   return (AccountInfoDouble(ACCOUNT_BALANCE) - InpCapital) / InpCapital * 100.0 >= TargetNeeded();
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

// ferme une position et attend (2 s max) que MT5 ne la liste plus : sinon un OPEN qui suit tout de suite
// (signal opposé) croit que la position existe encore et ne fait rien
bool CloseAndWait(ulong ticket)
  {
   if(!PositionSelectByTicket(ticket))
      return true;
   for(int attempt = 0; attempt < 3; attempt++)
     {
      trade.PositionClose(ticket);
      for(int i = 0; i < 20; i++)
        {
         if(!PositionSelectByTicket(ticket))
            return true;
         Sleep(100);
        }
     }
   return !PositionSelectByTicket(ticket);
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
      if(Stopped() || g_block_day)       { Print("LaboBot : signal ignoré (garde-fou actif) ", symbol); LogExec("OPEN", key, symbol, 0, 0, false, "garde-fou actif (perte du jour ou totale)"); return; }
      // objectif atteint : seulement les micro-trades (risque 0 = lot minimum) qui comptent les jours minimum FTMO
      if(TargetReached() && risk > 0)    { Print("LaboBot : objectif atteint, plus de nouveau trade"); LogExec("OPEN", key, symbol, 0, 0, false, "objectif atteint"); return; }
      if(age > InpDelaiMaxSec)           { Print("LaboBot : signal trop vieux (", age, " s) ignoré ", symbol); LogExec("OPEN", key, symbol, 0, 0, false, "signal trop vieux"); return; }
      if(dist <= 0 || side == 0)         { LogExec("OPEN", key, symbol, 0, 0, false, "signal invalide"); return; }
      if(ticket > 0)
        {
         // signal opposé : l'ancienne position de ce composant (CLOSE juste avant) peut encore être listée
         // par MT5 quelques instants -> on la ferme et on attend qu'elle disparaisse, puis on ouvre la nouvelle
         if(PositionSelectByTicket(ticket) && ((PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) == (side > 0)))
           { LogExec("OPEN", key, symbol, 0, 0, false, "déjà en position dans ce sens"); return; }
         if(!CloseAndWait(ticket))
           { LogExec("OPEN", key, symbol, 0, 0, false, "ancienne position impossible à fermer"); return; }
        }
      double stops = SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL) * SymbolInfoDouble(symbol, SYMBOL_POINT);
      if(dist <= stops)                  { Print("LaboBot : stop trop proche pour ", symbol); LogExec("OPEN", key, symbol, 0, 0, false, "stop trop proche"); return; }
      // même base que le paper trading : le solde (intérêts composés) ou le plus bas entre solde et capital de départ
      double bal = AccountInfoDouble(ACCOUNT_BALANCE);
      double risk_money = (InpComposer ? bal : MathMin(bal, InpCapital)) * MathMin(risk, InpRisqueMax) / 100.0;
      double lots = risk <= 0 ? SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN) : Lots(symbol, risk_money, dist, commission);
      if(lots <= 0)                      { Print("LaboBot : lot trop petit pour le risque demandé ", symbol); LogExec("OPEN", key, symbol, 0, 0, false, "lot trop petit"); return; }
      MqlTick tk;
      if(!SymbolInfoTick(symbol, tk))    { LogExec("OPEN", key, symbol, 0, 0, false, "pas de prix"); return; }
      double entry = side > 0 ? tk.ask : tk.bid;
      double sl = NormalizeDouble(entry - side * dist, digits);
      double tp = rr > 0 ? NormalizeDouble(entry + side * rr * dist, digits) : 0;
      trade.SetTypeFillingBySymbol(symbol);
      bool ok = side > 0 ? trade.Buy(lots, symbol, 0, sl, tp, "LB" + key)
                         : trade.Sell(lots, symbol, 0, sl, tp, "LB" + key);
      Print("LaboBot : ", side > 0 ? "ACHAT " : "VENTE ", lots, " ", symbol, " SL ", sl, " TP ", tp, " -> ",
            ok ? "OK" : "ÉCHEC", " (", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription(), ")");
      LogExec("OPEN", key, symbol, ok ? trade.ResultPrice() : 0, lots, ok,
              IntegerToString(trade.ResultRetcode()) + " " + trade.ResultRetcodeDescription());
      return;
     }
   if(ticket == 0 || !PositionSelectByTicket(ticket))
      return;                            // déjà fermée chez le courtier (SL / TP touché)
   long   type = PositionGetInteger(POSITION_TYPE);
   double cur_sl = PositionGetDouble(POSITION_SL), cur_tp = PositionGetDouble(POSITION_TP);
   double open_px = PositionGetDouble(POSITION_PRICE_OPEN);
   if(action == "CLOSE")
     {
      bool closed = CloseAndWait(ticket);
      Print("LaboBot : fermeture ", symbol, " (signal de la stratégie) -> ", closed ? "OK" : "ÉCHEC");
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
           "\nObjectif à atteindre : +", DoubleToString(TargetNeeded(), 2), " % (règle du meilleur jour comprise)",
           "\nPositions du bot : ", n,
           "\nDernier signal reçu : ", g_last_signal > 0 ? TimeToString(g_last_signal) : "aucun",
           "\nLe paper trading de la stratégie combinée (option C) doit tourner pour envoyer les signaux.");
  }
