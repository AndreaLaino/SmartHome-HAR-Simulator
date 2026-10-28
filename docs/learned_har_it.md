# Riconoscimento appreso e trasferimento tra case

Il dizionario descrive **che cosa sono gli oggetti**. Il modello impara **quali
attività corrispondono alle osservazioni**. Forno e microonde possono avere la
stessa categoria `Device_Cooking`, ma questa categoria, da sola, non avvia
«cooking». Senza un modello addestrato e attivato non vengono prodotte attività
apprese.

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
   **Train & evaluate on target houses**. Attendi il risultato e annota l'ID del
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
casa sorgente e premi **Train & evaluate on target houses**. **Temporal context**
è la durata della storia osservata in secondi simulati: `600` significa 10 minuti.
Leggi accuratezza, F1 e risultati per attività; senza dati target etichettati non
si può misurare il trasferimento.

Nella casa destinataria seleziona il modello salvato e premi **Activate in this
house**. Salva lo scenario. Le nuove osservazioni vengono elaborate con quel
modello senza riaddestrarlo. **Evaluate selected model on target sessions**
permette di valutare altre sessioni target mantenendo lo stesso modello.

La confidenza mostrata deriva dai voti della Random Forest e **non è calibrata**:
non equivale alla probabilità garantita che la predizione sia corretta, né
all'accuratezza misurata sui dati etichettati. Un modello incerto può astenersi.

## Attività simultanee e dizionario condiviso

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
| `har_data/models/` | Modelli addestrati e relativa configurazione. |
| `har_data/reports/` | Risultati della valutazione. |
| Scenario `.csv` | Pianta, oggetti, identità della casa, categorie personalizzate e ID del modello attivo. |

Il CSV contiene l'ID del modello, non tutti i suoi dati. Per spostare il progetto
su un altro computer copia gli scenari **e la directory `har_data/`**, inclusi
modelli e dizionario delle categorie personalizzate. Le copie che rappresentano
la stessa casa mantengono l'identità; per una casa nuova assegnane una nuova.
