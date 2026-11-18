# Riconoscimento appreso e trasferimento tra case

Il dizionario descrive **che cosa sono gli oggetti**. Il modello impara **quali
attività corrispondono alle osservazioni**. Forno e microonde possono avere la
stessa categoria `Device_Cooking`, ma questa categoria, da sola, non avvia
«cooking». Senza un modello addestrato e attivato non vengono prodotte attività
apprese.

La potenza usata dal riconoscimento arriva soltanto dagli Smart Meter, esterni
oppure integrati nelle luci smart. Senza contatore il dispositivo continua a
funzionare e consumare, ma HAR non legge il suo consumo interno. Le nuove
registrazioni non includono il campo interno `current_consumption`; questo campo
viene ignorato anche quando si elaborano vecchie registrazioni. Una misura
assente ha `power_observed = 0`, mentre una lettura valida di 0 W mantiene
`power_observed = 1`. Per gli esperimenti precedenti che utilizzavano il consumo
interno di dispositivi senza contatore, riaddestra il modello dalle sessioni
salvate prima di confrontare i risultati.

Anche lo stato ON/OFF dei dispositivi elettrici viene ricavato soltanto dalla
potenza misurata: oltre 0 W è ON, 0 W è OFF. Senza una lettura valida sono assenti
entrambe le informazioni; il valore numerico di riempimento 0 non significa
«spento», perché `power_observed = 0`. Un campione mancante esplicito non viene
sostituito dall'ultima lettura conservata nel sensore. Lo stato elettrico interno
non entra nei nuovi frame HAR e viene ignorato nelle vecchie registrazioni.
Riaddestra anche i modelli precedenti che utilizzavano direttamente ON/OFF.
Con questa convenzione, un consumo di standby maggiore di 0 W produce ON.

La finestra si apre da **Scenario → Learned activity recognition…** e ha tre
schede: **1 · Categories**, **2 · Record sessions**, **3 · Train & evaluate**.

## Una prova veloce con i dati dimostrativi

1. Avvia il simulatore con `python main.py`. Da **File → Open file**, apri
   [`source_oven_tv.csv`](../examples/learned_har/source_oven_tv.csv).
   La cucina contiene `source_oven`; il soggiorno contiene `source_tv`.
   Ogni stanza ha un PIR e uno Smart Meter collegato al dispositivo.
2. Apri **Scenario → Learned activity recognition…**. In **Categories** controlla
   `Device_Cooking` per il forno, `Device_Entertainment` per la TV,
   `Sensor_Presence` per i PIR e `Sensor_Power` per i contatori.
3. In **Train & evaluate**, premi **Add synthetic demo sessions**. Vengono
   aggiunte sessioni generate da uno script per la casa `demo-source` e per
   `demo-target`. Non sono registrazioni della casa aperta.
4. Lascia selezionata la sorgente **Demo: Oven / TV** e premi
   **Train & compare RF + Gradient Boosting**. Attendi il risultato e annota il nome del
   modello in **Saved model**. I dati destinatari vengono usati per il test,
   non per l'addestramento.
5. Apri da **File → Open file**
   [`target_microwave_console.csv`](../examples/learned_har/target_microwave_console.csv).
   La finestra HAR precedente viene chiusa. Riaprila: ora gli oggetti sono
   `target_microwave` e `target_console`, con le stesse categorie principali.
6. In **Train & evaluate → Saved model**, seleziona **lo stesso modello** e premi
   **Activate in this house**. Salva una copia dello scenario con **File → Save As**
   per conservare la selezione del modello.
7. Apri **Simulation → Manual** e premi **Start** nella finestra della simulazione.
   Fai clic sul centro del microonde per accenderlo; un altro clic lo spegne.
   Spostati con clic nella stanza; la porta aperta tra cucina e soggiorno è al
   centro della parete divisoria. Prova anche la console.
8. Apri **Simulation → Activity Log** per vedere le attività prodotte dal modello.
   Controlla anche la presenza rilevata dai PIR e i consumi dei contatori.

**Questa prova verifica il percorso tecnico con dati sintetici. Le sue metriche
non misurano l'accuratezza su persone reali.** Il modello può sbagliare anche nella
prova manuale: le osservazioni interattive possono differire dai dati generati.
Il pulsante demo non attiva automaticamente un modello e non modifica la casa
aperta. Premerlo nuovamente sostituisce le stesse sessioni dimostrative.

Negli esempi tutti i dispositivi partono spenti e nessun modello è preattivato.
Le etichette delle stanze sono salvate esplicitamente. Microonde, TV e console
hanno profili predefiniti illustrativi in `consumption_profiles.py` e rimangono
accesi fino allo spegnimento manuale. Il tostapane termina automaticamente il
proprio ciclo, impostato a tre minuti simulati. Questi profili non sono misure
reali di un particolare prodotto.

## Registrare una prova propria

### Pannello rapido sotto la mappa

Premi il **cappello di laurea** nella barra a sinistra per aprire il pannello.
Seleziona una o più attività realmente svolte, scegli **Source (training)** per
la casa sorgente oppure **Target (test)** per le prove nella casa destinataria,
poi premi **Record**. Il pannello apre Manual e avvia o riprende l'orologio.
Durante la registrazione cambia i pulsanti quando cambia l'attività: le etichette
valgono da quel momento, senza modificare le osservazioni precedenti.

**No activity (idle)** indica esplicitamente che nessuna attività è in corso.
Nessun pulsante selezionato lascia invece le osservazioni senza etichetta.
**Add activity** aggiunge un nome riutilizzabile anche nelle altre case; i nomi
personalizzati sono salvati in `har_data/activities.json`. Per riconoscere una
nuova attività occorre raccogliere esempi etichettati e addestrare il modello.
I pulsanti non impongono le predizioni del modello.

Sono disponibili 12 attività predefinite: `cooking`, `eating`, `relaxing`,
`sleeping`, `working`, `cleaning`, `laundry`, `washing dishes`,
`using the bathroom`, `showering`, `leaving home` e `returning home`.
Il pannello aggiunge anche i nomi personalizzati e quelli presenti nelle
registrazioni e nei modelli salvati. La presenza del pulsante non significa che
il modello abbia già imparato quell'attività.

**Stop & save** salva la sessione e termina la registrazione; l'orologio conserva
il proprio controllo Start/Stop. Prima di una nuova sessione indipendente premi
**Reset**. Richiudere il pannello con il cappello non interrompe la registrazione.
**Train & models…** apre la finestra esistente sulla scheda di addestramento.
Da lì restano accessibili anche categorie, nome e identità della casa.

### 1. Categorie e identità della casa

Apri la casa sorgente. Nella scheda **Record sessions**, scrivi un nome e premi
**New house identity for a copied scenario**, quindi salva lo scenario separato.
Fallo anche per la casa destinataria, con un'identità diversa. **Per prove proprie
non riutilizzare le identità degli esempi dopo aver aggiunto i dati demo:** le
sessioni della stessa casa vengono raggruppate e mescoleresti dati tuoi e sintetici.
Mantieni la stessa identità tra tutte le sessioni della medesima casa.

In **Categories**, seleziona un oggetto e scegli una o più categorie:

- **Apply selected categories to object** salva una scelta per quell'oggetto;
  salva anche lo scenario per conservarla.
- **Save as defaults for this object type** salva la scelta nel dizionario
  condiviso: altri oggetti di quel tipo possono riutilizzarla.
- **Use shared type defaults** ripristina l'uso dei valori condivisi.

Nessuna categoria selezionata significa oggetto non classificato. Un computer
può avere più categorie contemporaneamente. Non si seleziona un'attività da
trasferire tra dispositivi.

Le categorie predefinite usano i prefissi `Device_` e `Sensor_`, ad esempio
`Device_Cooking` e `Sensor_Presence`. I vecchi nomi senza `_` restano leggibili:
il dizionario condiviso viene aggiornato conservando le personalizzazioni.
Registrazioni e modelli già salvati mantengono il proprio dizionario e schema;
le osservazioni equivalenti vengono adattate ai loro nomi senza riaddestrarli.

### 2. Sessioni della casa sorgente

1. Apri **Simulation → Manual**. In **Record sessions**, seleziona **source** e
   premi **Start recording**; poi premi **Start** nella simulazione.
2. Prima di **Apply labels**, le osservazioni sono contesto senza etichetta
   (`labels: null`): vengono conservate ma non sono esempi supervisionati.
3. Per un tratto senza attività da riconoscere, lascia vuoti i nomi, seleziona
   **No activity (explicit ground truth)** e premi **Apply labels** (`labels: []`).
4. Quando inizi a cucinare, togli la spunta, scrivi `cooking` in **True activities
   now** e premi **Apply labels**. Interagisci con il forno e sposta la persona
   nella cucina. Quando cambia l'attività, applica subito le nuove etichette.
   Per rilassarti davanti alla TV usa, per esempio, `relaxing`.
5. Registra anche esempi contrari: presenza senza utilizzo, apparecchio spento,
   oppure acceso mentre la persona svolge un'altra attività. Etichetta ciò che
   la persona sta facendo, non ciò che suggerisce il nome del dispositivo.
6. Premi **Stop & save session**, poi **Reset** nella simulazione. Riapri Manual
   per la sessione successiva. Servono **almeno tre sessioni indipendenti**;
   fermare e riavviare soltanto la registrazione non crea una nuova simulazione.

Usa le stesse etichette, scritte nello stesso modo, in tutte le case. Ogni
sessione dovrebbe contenere esempi positivi e negativi delle attività: le sessioni
intere vengono separate tra training, validazione e test, quindi con sole tre
sessioni una sola finisce nel training. Varia azioni e durate invece di copiare
le registrazioni. Più dati indipendenti permettono una valutazione più utile.

Le etichette valgono dal momento dell'applicazione, senza correggere il passato.
Un campo vuoto non equivale automaticamente a «nessuna attività».

### 3. Casa destinataria, addestramento e utilizzo

Apri la casa destinataria, controlla categorie e identità, e registra una o più
sessioni scegliendo **target**. Etichettale nello stesso modo: queste etichette
servono soltanto a verificare le predizioni. Non vengono usate per addestrare.

In **Train & evaluate**, aggiorna con **Refresh sessions / models**, scegli la
casa sorgente e premi **Train & compare RF + Gradient Boosting**. **Temporal context**
è la durata della storia osservata in secondi simulati: `600` significa 10 minuti.
Leggi accuratezza, precision, recall, F1 e coverage di entrambi gli algoritmi;
senza dati target etichettati non
si può misurare il trasferimento.

Il training crea una coppia Random Forest / Gradient Boosting con gli stessi
ingressi, la stessa finestra temporale e le stesse sessioni separate per training,
validazione e test. Ogni algoritmo impara un classificatore binario per attività,
con soglia di decisione `0.5` e confidenza minima `0.55`, senza ottimizzarle sui
dati target. I parametri dei due algoritmi sono fissi e salvati insieme al seed
e alla versione di scikit-learn: questa è una prova di confronto, non una ricerca
automatica del modello migliore. Addestrare entrambi richiede più tempo.
Per scegliere tra gli algoritmi usa la validazione della casa sorgente, mostrata
nel report. Mantieni le sessioni target per la verifica finale del trasferimento,
senza usarle per scegliere parametri o soglie.

Nella casa destinataria seleziona il modello salvato e premi **Activate in this
house**. Salva lo scenario. Le nuove osservazioni vengono elaborate con quel
modello senza riaddestrarlo. **Evaluate selected model on target sessions**
permette di valutare altre sessioni target mantenendo lo stesso modello.

Attivando la nuova coppia, in Manual compaiono due colonne in basso a destra:
**Random Forest** a sinistra e **Gradient Boosting** a destra. Entrambe ricevono
le stesse osservazioni nello stesso istante simulato. Le intestazioni restano
visibili anche senza un confronto attivo. Con un vecchio modello solo Random
Forest, la colonna Gradient Boosting indica di attivare un confronto salvato;
non mostra una predizione finché quel classificatore non è attivo. I vecchi
modelli rimangono utilizzabili singolarmente. L'Activity Log esistente conserva gli intervalli
della Random Forest; il log `har_comparison.jsonl` conserva le predizioni di entrambi.

Prima del training compare una finestra per scegliere il nome del confronto.
Il campo propone `NomeCasa_[IDcasa]_YYYY-MM-DD_`, usando il nome della casa
sorgente selezionata, i primi otto caratteri del suo ID persistente e la data
locale. Per esempio: `Casa_A_[3c5e83fe]_2026-11-18_`. L'ID è quello della casa,
condiviso tra le sue sessioni, non quello della singola registrazione. Se il nome
HAR è ancora il generico `House`, viene usato il nome del file dello scenario
aperto quando appartiene alla stessa casa sorgente. Il cursore è alla fine:
puoi aggiungere una descrizione oppure
cancellare tutto e scrivere un nome completamente diverso. **Train & save**
avvia il training e salva la coppia con quel nome; **Cancel** chiude la finestra
senza avviare il calcolo. I nomi vuoti o già utilizzati vengono segnalati per
evitare selezioni ambigue. Il nome scelto compare nel selettore e nello stato
del modello attivo.

I vecchi modelli mantengono i propri nomi, come `House_A_2026-10-28_001`.
Quando manca un nome salvato, viene generato da casa sorgente, data e numero
progressivo. Per i vecchi modelli,
quando manca il nome della casa nei metadati, viene recuperato dalle registrazioni
sorgenti. Se queste informazioni non sono disponibili, il nome indica
`Unknown_house` o `undated`. UUID, file JSON e riferimenti negli scenari restano
invariati; i nuovi modelli salvano anche nome leggibile e nome della casa sorgente.

La confidenza mostrata da entrambi gli algoritmi **non è calibrata**
(per Random Forest deriva dai voti degli alberi):
non equivale alla probabilità garantita che la predizione sia corretta, né
all'accuratezza misurata sui dati etichettati. Un modello incerto può astenersi.

## Contesto degli oggetti nel campo dei PIR

Le nuove registrazioni salvano per ogni PIR le categorie degli oggetti compresi
nel suo campo di rilevazione. Il controllo usa portata e apertura del simulatore
(`230` unità logiche e `60°`), orientamento, muri e porte chiuse. Un oggetto viene
incluso se il suo punto di posizionamento nella mappa è visibile nel campo; non
si usa la distanza minima da tutti gli oggetti della stanza.

Il modello riceve così ingressi distinti di presenza per gli oggetti coperti:
dispositivi da cottura, intrattenimento, sedute, letti e altre categorie del
dizionario. Non occorre dividere l'ambiente in zone o assegnare attività ai PIR.
Nomi e coordinate servono a ricavare le relazioni nella singola casa, ma non
diventano ingressi del classificatore. Il metodo non usa la posizione dell'avatar
per determinare quale oggetto la persona stia utilizzando.

Un PIR che copre più categorie contribuisce a tutte, conservando l'ambiguità.
Più oggetti della stessa categoria non moltiplicano il peso di quel PIR. Lo stato
viene aggregato per categoria insieme a indicatori di disponibilità e misura
osservata; il modello mantiene le statistiche temporali anche su questi ingressi.
Le relazioni vengono congelate a ogni osservazione: cambiamenti successivi della
scena non riscrivono il training e una porta aperta o chiusa può cambiare la
copertura delle osservazioni successive.

Per provarlo:

1. Assegna categorie corrette agli oggetti e controlla i campi visivi dei PIR.
2. Registra almeno tre nuove simulazioni sorgenti indipendenti, con esempi di
   presenza senza attività e apparecchi accesi mentre la persona è altrove.
3. Addestra la coppia e controlla la riga **PIR object context observed in N/M
   labelled training observations**. Se `N` è zero, il training non dispone di
   questi segnali: verifica orientamento, copertura e registrazioni utilizzate.
4. Attiva il nuovo confronto e valuta una casa destinataria indipendente, senza
   usarne le etichette per scegliere ingressi o parametri.

Le registrazioni precedenti non contengono questo contesto: non viene ricostruito
usando la casa aperta, perché mancano orientamenti e ostacoli congelati. Una
geometria sconosciuta, un campo noto senza oggetti e una lettura mancante restano
distinti. I nuovi modelli usano lo schema degli ingressi versione 2; i vecchi
modelli restano utilizzabili con il loro schema originale. Riaddestrare sui soli
dati precedenti non insegna il nuovo contesto. Più informazione disponibile non
garantisce maggiore accuratezza: va verificata con il confronto sperimentale tra
modalità previsto da WP3/E4 del progetto teorico.

## Attività simultanee e dizionario condiviso

I nuovi confronti addestrano una Random Forest e un Gradient Boosting binari
indipendenti per ogni attività. Ogni albero confronta tutti gli ingressi quando sceglie una divisione:
il segnale più informativo per quell'attività non viene escluso da un sottoinsieme
casuale dei canali di categoria. Non esistono associazioni manuali sensore-attività;
sono i dati etichettati della sorgente a determinare le divisioni degli alberi.

Ogni attività ha una propria confidenza e una decisione di astensione. Se
`eating` è incerta e `relaxing` è riconosciuta con sicurezza, il simulatore mostra
e registra `relaxing`, indicando separatamente l'incertezza su `eating`.
Quando entrambe sono riconosciute, compaiono entrambe come attività distinte.
La confidenza accanto a ciascuna attività resta non calibrata. La coverage per
attività nel report indica la quota di osservazioni con una decisione sicura;
l'accuratezza dell'insieme completo richiede che tutte le attività siano decise.

Questa struttura permette rilevamenti simultanei anche senza aver dato un nome
nuovo alla loro presenza contemporanea. Per attività simultanee mai osservate
nel training la qualità dipende comunque da segnali distinguibili e dati vari:
non viene garantita dal solo cambio di classificatore. I test sintetici verificano
un caso controllato con due canali sensoriali diversi e una casa destinataria con
nomi e dispositivi differenti; non misurano l'accuratezza su persone reali.

I vecchi modelli di versione 1 restano utilizzabili con il comportamento originale,
compresa l'astensione sull'intero insieme di attività. Per usare i nuovi rilevatori,
addestra e attiva un nuovo modello dalle registrazioni sorgenti già salvate.
I modelli precedenti non vengono modificati automaticamente.

Per attività simultanee puoi applicare più etichette, ad esempio
`cooking, relaxing`. Non esistono regole automatiche per dedurre «eating» dopo
«cooking» o per creare attività composte: eventuali etichette aggiuntive devono
comparire negli esempi di training con osservazioni utili a distinguerle.

Ogni sessione salva una copia del dizionario e le categorie effettive degli
oggetti; il modello conserva il proprio dizionario. Cambiare successivamente i
valori predefiniti non riscrive i dati già registrati. Le categorie dei sensori
devono mantenere misura e unità coerenti tra case: per esempio potenza in `W`,
temperatura in `°C`, presenza binaria. Il sistema rifiuta categorie con lo stesso
nome ma misura o unità differenti; non converte automaticamente `kW` in `W`.
Per i sensori Weight la scala dipende dalla configurazione: non viene assunta
una misura in chilogrammi.

## Dove vengono salvati i dati

| Percorso | Contenuto |
|---|---|
| `har_data/catalogue.json` | Dizionario globale delle categorie e valori predefiniti per tipo. |
| `har_data/sessions/` | Osservazioni, etichette e dizionario congelato di ogni registrazione. |
| `har_data/activities.json` | Nomi personalizzati dei pulsanti attività, condivisi tra case. |
| `har_data/models/` | Coppie di modelli addestrati, configurazione, seed, parametri e impronte delle sessioni sorgenti; anche i modelli precedenti sono conservati. |
| `har_data/reports/` | Metriche e predizioni per algoritmo, con validazione, test sorgente e test target distinti. |
| `saves/<sessione>/har_comparison.jsonl` | Previsioni affiancate con tempo simulato, ID della coppia e dei modelli, attività e incertezze. `expected` contiene le etichette umane solo durante una registrazione, altrimenti `null`. |
| Scenario `.csv` | Pianta, oggetti, identità della casa, categorie personalizzate e ID del modello attivo. |

Il CSV contiene l'ID del modello, non tutti i suoi dati. Per spostare il progetto
su un altro computer copia gli scenari **e la directory `har_data/`**, inclusi
modelli e dizionario delle categorie personalizzate. Le copie che rappresentano
la stessa casa mantengono l'identità; per una casa nuova assegnane una nuova.
