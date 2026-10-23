# Inventario delle modifiche al simulatore

Verificato sul codice locale il 21 ottobre 2026.

Questo inventario descrive il percorso discusso: illuminazione, monitoraggio
della potenza, modifiche all'interfaccia e al salvataggio, proprietà, riconoscimento
delle attività, trasferimento tra abitazioni e strumenti di valutazione.
Le funzionalità preesistenti non diventano nuove solo perché sono utilizzate dalle
estensioni: per esempio, forno, computer e lavatrice erano già presenti; sono state
aggiunte le loro proprietà e l'integrazione nel nuovo riconoscitore.

La ricostruzione usa i commit `6d9c03e` e `9083193`, confrontati con `28e77da`,
e le successive modifiche locali. Le estensioni più recenti al trasferimento
esplicito e agli esperimenti sono ancora presenti nel working tree.

## 1. Tipi di illuminazione

- Aggiunti Normal Light e Smart Light al catalogo e alla finestra di creazione.
- Conservato il supporto al precedente tipo Lighting come luce normale.
- Aggiunta la potenza configurabile delle luci, con valore iniziale di 10 W.
- Accettati valori frazionari e controllati i valori non validi.
- La luce accesa assorbe la potenza configurata; spenta assorbe 0 W.
- Lo stato resta impostato finché cambia un comando o, per il controllo automatico,
  cambia la presenza; non viene trattata come un ciclo di consumo temporaneo.

## 2. Controllo manuale e automatico

- Separato il tipo di luce dalla modalità di controllo: entrambe le tipologie
  possono essere manuali o automatiche.
- Aggiunto il comando Manual/Automatic alla creazione e alla modifica della luce.
- Le luci manuali possono essere accese e spente mediante interruttore.
- Le luci automatiche seguono la presenza nella stanza.
- Quando l'avatar è disponibile, la sua posizione è la fonte di presenza per la
  simulazione locale; prima del suo posizionamento si possono usare PIR attivi.
- Nelle aree senza stanze riconosciute viene usata la presenza vicina, limitata
  dalla distanza e dalla visibilità attraverso muri e porte.
- Le configurazioni precedenti mantengono il controllo manuale.

## 3. Interruttori delle luci

- Introdotto un oggetto interruttore associato a una luce, con nome e posizione.
- Consentiti più interruttori per una stessa luce.
- Consentito trascinare un interruttore indipendentemente dalla luce.
- Spostare la luce lascia gli interruttori nelle loro posizioni.
- Aggiunte le azioni Add switch e Remove selected switch.
- Aggiunta la cancellazione dell'interruttore selezionato dalla mappa.
- Integrate aggiunte, spostamenti e cancellazioni nella cronologia Undo/Redo.
- Copiare una luce crea copie indipendenti dei suoi interruttori; copiare un solo
  interruttore conserva il collegamento alla luce originale.
- Salvate le coordinate, le associazioni e anche una lista esplicitamente vuota.
- Gli interruttori conservati per una luce automatica tornano utilizzabili quando
  il controllo passa a Manual.
- Aggiunta la preferenza di conferma per la cancellazione degli interruttori.

## 4. Finestra di dettaglio delle luci

- Apertura mediante doppio clic sulla luce.
- Visualizzazione di nome, tipo, stato, posizione, potenza nominale e corrente,
  intervallo di consumo, stanza, metering, letture e sensori associati.
- Elenco degli interruttori con coordinate e stato.
- Cambio della modalità di controllo e gestione degli interruttori dalla finestra.
- Collegamenti a grafici, CSV/log e proprietà per il riconoscimento.
- Aggiornamento periodico dei dati e chiusura della finestra se la luce viene rimossa.

## 5. Metering integrato delle smart light

- Generazione automatica di uno Smart Meter interno alla Smart Light.
- Il meter segue nome, posizione, potenza e stato del dispositivo.
- Nessun posizionamento separato del meter interno sulla mappa.
- Integrazione delle letture nel runtime, nei grafici, nei log e nei totali stanza.
- Ricostruzione del meter dopo copia e caricamento dello scenario.
- Gestione di collisioni tra nomi e prevenzione della creazione ripetuta del meter.
- Rimozione della relativa cronologia quando la smart light viene cancellata.
- Prevenzione del doppio conteggio della smart light se esiste anche un meter esterno.
- Le luci normali possono funzionare senza meter, ma contribuiscono ai totali
  misurati soltanto quando hanno uno Smart Meter separato.

## 6. Potenza generale delle stanze

- Aggiunto il totale di potenza istantanea della stanza sommando gli Smart Meter.
- Il totale comprende luci e altri dispositivi misurati.
- Il meter associato a un dispositivo viene attribuito alla stanza del dispositivo.
- Un meter non associato usa la propria posizione.
- Ogni meter viene attribuito al massimo a una stanza; sui confini si usa un
  ordinamento stabile degli identificativi delle stanze.
- Escluse letture negative, non finite o non numeriche e oggetti fuori dalle stanze.
- Aggiornamento dei totali dopo spostamenti, variazioni delle letture e rimozioni.
- Esclusione dei dispositivi non misurati dal totale degli Smart Meter.
- Visualizzazione sulla mappa e nella finestra Recognize rooms.
- Distinzione tra stanza senza meter e stanza misurata a 0 W.
- I valori rappresentano potenza in W; non è stato aggiunto un integratore di energia in kWh.

## 7. Cancellazione e cronologia delle modifiche

- Aggiunti pulsanti Undo, Redo e Delete nella palette.
- Collegati i comandi macOS: Cmd+Z, Cmd+Shift+Z e Cmd+Delete/Canc.
- Supportati anche Ctrl+Z, Ctrl+Shift+Z/Ctrl+Y e i tasti di cancellazione previsti.
- Gestiti più passi consecutivi; una nuova modifica elimina il ramo Redo.
- Estesa la cronologia a spostamenti, ridimensionamenti, creazione di muri,
  cancellazioni, operazioni di incolla/duplicazione e interruttori.
- Il ripristino di punti conserva anche muri e porte collegati.
- Il ripristino di dispositivi/sensori recupera collegamenti e stato pertinente
  della simulazione, inclusa la cronologia dei meter gestita dalla cancellazione.
- Le scorciatoie sulla mappa non sostituiscono la normale modifica del testo nei campi.

## 8. Correzioni al salvataggio

- Su macOS sostituito il percorso del pannello nativo di Save As con un browser
  di file costruito con widget Tk, per evitare il crash segnalato all'apertura.
- Gestiti scelta della cartella, nome, estensione .csv, annullamento ed errori.
- Usata un'attesa modale limitata al dialogo, senza terminare il mainloop dell'app.
- Differito l'avvio del salvataggio dal menu con after_idle per uscire dalla
  gestione del menu macOS prima di aprire dialoghi modali.
- Preparato l'intero CSV prima di modificare il file di destinazione.
- Scrittura in un file temporaneo, flush/fsync e sostituzione atomica.
- In caso di errore viene preservato il file precedente, ripulito il temporaneo
  e mostrato un messaggio; il percorso corrente cambia solo dopo il successo.
- Conservato il percorso dei dialoghi esistente sulle altre piattaforme.

## 9. Leggibilità e grafici

- Aumentato il contrasto dei testi dei controlli disabilitati nel tema chiaro.
- Adeguato il colore dei pulsanti primari/pericolosi anche quando disabilitati.
- Per luci e dispositivi statici introdotta l'indicazione Mode: Static nei grafici,
  evitando di presentare metadati LLM non applicabili con segnaposto interrogativi.
- Integrati i dati delle luci e dei loro meter nei collegamenti a grafici e log.

## 10. Catalogo di oggetti esteso

- Aggiunti Microwave, TV, PlayStation, Vacuum Cleaner, Kettle, Toaster, Hair Dryer,
  Shower, Toilet, Sink, Bed, Dining Table e Sofa.
- Aggiunte proprietà a dispositivi già esistenti, inclusi forno, computer,
  frigorifero, lavatrice, lavastoviglie e macchina del caffè.
- Introdotto il comportamento a potenza costante dei nuovi dispositivi elettrici
  statici durante l'accensione.
- Arredi e sanitari del modello hanno 0 W; il loro uso viene osservato attraverso
  presenza, peso, contatto o flusso.
- Adeguata l'interpretazione delle stanze ai nuovi oggetti e sanitari.

## 11. Proprietà multiple e pesi

- Aggiunto un dizionario di proprietà per ogni dispositivo e sensore.
- Consentite più proprietà per un oggetto; per esempio il Computer ha working=0.85
  e relaxing=0.55 come valori iniziali.
- Introdotti pesi di proprietà nell'intervallo 0–1 e un peso generale dell'oggetto.
- Controllati nomi vuoti, valori fuori intervallo e numeri non finiti.
- Definito il formato di inserimento nome=peso, con più valori separati da virgole.
- Normalizzati maiuscole, spazi, separatori e alcuni alias espliciti, per esempio
  cooking device/cooking e work/working.
- Normalizzati alcuni tipi noti, per esempio PC/Computer e console/PlayStation.
- Aggiunti campi alla creazione e l'editor Properties / recognition weights…
  per oggetti già presenti.
- Consentita la scelta di proprietà locali o di un profilo condiviso.
- Le copie mantengono dizionari di proprietà indipendenti.

## 12. Osservazioni dei sensori

- Collegato il nuovo riconoscitore alle osservazioni della simulazione.
- Usati gli Smart Meter per osservare l'utilizzo elettrico; per l'attivazione
  dell'evidenza del meter è prevista una soglia superiore a 0.5 W.
- Integrato Weight per letto, sedute/tavolo e fixture pertinenti.
- Aggiunto Water Flow al catalogo e alla simulazione locale di doccia/lavandino.
- Nella simulazione locale la vicinanza/interazione con la fixture attiva il
  flusso, e l'allontanamento lo disattiva.
- Usati contatto/peso associati al sanitario come evidenza pertinente.
- Consentita la modifica dell'associazione sensore-dispositivo nell'editor proprietà.
- Gestite prossimità, stanza e ostacoli nei fallback di osservazione.
- Per un dispositivo attivo senza meter è disponibile un fallback basato sulla
  presenza vicina; se un meter è presente, la sua lettura resta autorevole.
- Corretto il caso standby/zero: la sola combinazione dispositivo ON + presenza
  non può riattivare l'evidenza esclusa dal meter.
- Per più osservazioni dello stesso ruolo si usa la forza massima, evitando che
  sensori ripetuti aumentino artificialmente il supporto.

## 13. Motore di attività e regole

- Introdotto il motore di evidenze e regole pesate in activity_recognition.py.
- Gestite le attività: cooking, eating, eating while relaxing, working, relaxing,
  laundry, dishwasher, cleaning, using the bathroom, taking a shower, washing hands,
  preparing a drink, grooming e sleeping.
- Separati ruoli osservati, pesi delle evidenze, peso dell'attività, condizioni
  obbligatorie e soglia di attivazione.
- Il supporto è la media pesata delle evidenze, moltiplicata per il peso della regola
  e limitata a 1; la soglia iniziale delle regole standard è 0.65.
- Consentite attività simultanee quando le regole le supportano.
- Conservate separatamente eating e relaxing quando la composita è riconosciuta;
  laundry/dishwasher prevalgono sulla generica cleaning.
- Quando la persona è fuori vengono soppresse le attività personali, mentre
  laundry, dishwasher e cleaning possono continuare come attività di background.
- Resettata la memoria pertinente quando cambia il modello o il tempo torna indietro.

## 14. Sequenze temporali e inferenze alimentari

- Memorizzati attivazione, sorgenti e forza dell'evidenza di cucina/intrattenimento.
- Introdotta la memoria della cucina recente, con decadimento nel tempo.
- Finestra iniziale configurabile di 60 minuti simulati.
- Inferita eating dopo cucina recente e presenza, quando la cucina non è più attiva.
- Inferita eating while relaxing quando l'intrattenimento inizia dopo la cucina.
- L'ordine inverso o l'avvio simultaneo non soddisfano automaticamente la sequenza.
- Conservata la provenienza della precedente evidenza di cucina per le spiegazioni.
- Marcate queste decisioni come inferite: il passaggio cucina→pasto è l'ipotesi
  comportamentale richiesta, non una misura diretta dell'ingestione del cibo.

## 15. Condizioni per il sonno

- Richiesta presenza in una stanza classificata Bedroom.
- Richiesta evidenza di peso sul letto.
- Richieste luci spente e potenza delle luci nulla.
- Richiesta continuità delle condizioni per 10 minuti simulati come valore iniziale,
  modificabile nelle impostazioni del modello.
- Condizioni mancanti o assenza di luci/stanza non vengono interpretate come conferma.
- Una lettura positiva di un meter associato a una luce impedisce la conferma,
  anche se lo stato del dispositivo risultasse OFF.
- Il controllo riguarda l'illuminazione: carichi estranei come il frigorifero
  non devono azzerarsi per riconoscere il sonno.

## Come leggere i punti dal 16 in poi

Questa parte contiene tre lavori diversi. Per orientarti, parti da questa tabella:

| Punti | Domanda a cui rispondono |
| --- | --- |
| 16–20 | Come riconosce le attività l'app che uso durante la simulazione? |
| 21–22 | Come controllo se le attività riconosciute sono corrette? |
| 23–28 | Come confronto metodi diversi e salvo un esperimento ripetibile? |

**L'app durante la simulazione usa regole e riferimenti configurati.** I Random
Forest sono presenti in uno strumento di confronto separato: eseguire un
esperimento non sostituisce automaticamente il riconoscitore dell'app.

Ogni punto qui sotto spiega il significato pratico. I dettagli di implementazione
originali, comprese formule e parametri, sono conservati nell'appendice finale.

## 16. Riconoscere la stessa attività usando dispositivi diversi

**Che cosa abbiamo cambiato.** Abbiamo aggiunto un confronto tra un oggetto noto e
un oggetto diverso che potrebbe svolgere lo stesso compito.

Esempio: conosciamo l'associazione «uso del forno → indizio di cucina». Nella
seconda casa c'è un microonde. Descriviamo noi le sue capacità, per esempio
«riscalda alimenti» e «prepara alimenti». Quando un sensore ne osserva l'utilizzo,
il sistema controlla se queste capacità soddisfano quelle richieste dal
riferimento del forno. Se il confronto riesce, il microonde può fornire lo stesso
indizio alle regole che riconoscono il cucinare.

La distinzione tra un indizio e la decisione finale serve perché le regole possono
richiedere anche presenza, altre osservazioni o una certa storia temporale.

**Che cosa significa un peso.** Possiamo indicare che preparare alimenti conta più
di produrre genericamente calore. Possiamo anche rendere una capacità
obbligatoria: così un asciugacapelli non viene accettato soltanto perché scalda.

**Che cosa resta deciso da noi.** Inseriamo noi le capacità degli oggetti,
l'associazione nota con il forno e i criteri del confronto. Questo passaggio
riutilizza conoscenza configurata; non scopre da solo a cosa serve il microonde.
Il confronto con il forno non è indispensabile se scriviamo una regola equivalente
direttamente sulle capacità. Non è stata dimostrata una sua superiorità generale.

Per il report: «È stato aggiunto un meccanismo di sostituzione tra oggetti basato
sul confronto pesato delle loro capacità e su associazioni di riferimento esplicite».

## 17. Condividere le stesse impostazioni tra case diverse

**Che cosa abbiamo cambiato.** Abbiamo creato un archivio comune, nella directory
`recognition_library/`, per riutilizzare proprietà e regole in più abitazioni.
Il file principale è `library.json`: viene usato anche dal metodo attuale con i
riferimenti.

Esempio: la casa A contiene forno e TV; la casa B contiene microonde e PlayStation.
Entrambe possono usare le stesse definizioni di capacità, associazioni e attività.
Le posizioni, i dispositivi concreti e le osservazioni restano propri di ogni casa.

È possibile scegliere una casa sorgente, registrare un'altra casa con un'identità
nuova e collegarle allo stesso archivio. Le modifiche locali possono prevalere su
quelle condivise. Scollegando una casa, la configurazione effettiva viene
conservata localmente.

Per il report: «Il modello condiviso è stato separato dalle singole abitazioni,
consentendo di riutilizzare proprietà e regole senza copiare la disposizione della casa».

## 18. Vedere perché l'app ha riconosciuto un'attività

**Che cosa abbiamo cambiato.** Abbiamo aggiunto finestre che mostrano la decisione
insieme alle informazioni che l'hanno prodotta.

Da **Scenario → Activity recognition…** puoi vedere quali attività sono attive,
quali sensori le supportano e quali condizioni mancano. Con un doppio clic puoi
leggere, per esempio: «il microonde è stato confrontato con il forno, queste
proprietà corrispondono e questo Smart Meter ne ha rilevato l'utilizzo».

Puoi modificare proprietà, pesi, soglie e associazioni dalle finestre dedicate.
Per “soglia” si intende il valore minimo richiesto per accettare una decisione.
Le associazioni possono essere locali alla casa oppure condivise nella libreria.

Per il report: «Sono state introdotte interfacce per configurare il riconoscimento
e consultare le evidenze e i riferimenti che giustificano ogni decisione».

## 19. Distinguere possibilità, confidenza e correttezza

**Che cosa abbiamo cambiato.** Abbiamo separato quantità che rispondono a domande diverse:

| Quantità | Domanda pratica |
| --- | --- |
| Similarità delle proprietà | Il microonde soddisfa le capacità rilevanti richieste dal riferimento? |
| Supporto e copertura delle evidenze | Quanto sostengono la regola gli indizi presenti e quali indizi mancano? |
| Confidenza della decisione | Quanto è sostenuta questa risposta secondo il punteggio del riconoscitore? |
| Accuratezza misurata | Quante risposte coincidono con attività corrette indicate indipendentemente? |

La confidenza del riconoscitore a regole deriva dai pesi e dalle evidenze. Un
valore dell'80% **non dimostra** che la decisione sia corretta nell'80% dei casi.
È possibile fissare un minimo sotto il quale l'app non conferma l'attività.

Il **Transfer report** controlla se una casa ha capacità e strumenti di
osservazione sufficienti per poter riconoscere certe attività. Non misura quante
volte le riconoscerà correttamente durante un esperimento.

Per il report: «Sono stati introdotti punteggi di supporto e confidenza e un
report di trasferibilità potenziale, distinti dalla valutazione dell'accuratezza».

## 20. Salvare anche le impostazioni del riconoscimento

**Che cosa abbiamo cambiato.** Il salvataggio dello scenario conserva anche le
informazioni aggiunte durante questo lavoro: proprietà, pesi, regole, associazioni
di riferimento, collegamento alla libreria e configurazione delle luci.

Esempio: assegni al microonde certe capacità, modifichi una soglia e salvi la casa.
Riaprendo il CSV puoi ritrovare queste impostazioni, invece di doverle reinserire.
Se la casa resta collegata alla libreria, i profili e le regole condivisi possono
continuare ad aggiornarsi secondo quel collegamento. La lettura degli scenari
precedenti resta supportata.

Per il report: «Il formato degli scenari è stato esteso per conservare le nuove
proprietà e configurazioni del riconoscimento, mantenendo la compatibilità con i file precedenti».

## 21. Registrare quale attività stava realmente avvenendo

**Che cosa abbiamo cambiato.** Abbiamo aggiunto un modo per confrontare la risposta
dell'app con un'attività corretta indicata dall'utente.

Esempio: stai simulando una persona che cucina. Registri quell'istante con
l'etichetta `cooking`, indipendentemente da ciò che dice il riconoscitore.
Questa risposta corretta è detta **etichetta**; l'insieme delle etichette corrette
è spesso chiamato *ground truth*.

La registrazione conserva le osservazioni e la descrizione della casa insieme
all'etichetta. Puoi indicare più attività oppure dichiarare esplicitamente che
non sta avvenendo alcuna attività. Un istante senza etichetta rimane sconosciuto:
non viene considerato automaticamente un esempio di “nessuna attività”.

Il pulsante **Record human-labelled frame** etichetta soltanto l'istante corrente,
non tutto un intervallo. Osservazioni precedenti vengono mantenute come contesto.
Per registrazioni indipendenti occorre resettare e riavviare la simulazione.

Per il report: «È stato introdotto un sistema di registrazione delle osservazioni
con etichette manuali indipendenti dalle predizioni».

## 22. Misurare se il riconoscitore risponde correttamente

**Che cosa abbiamo cambiato.** Abbiamo aggiunto una valutazione che rilegge le
osservazioni registrate, esegue il riconoscimento e confronta le risposte con le
etichette corrette.

Esempio: in un istante hai indicato `cooking`, ma l'app risponde `relaxing`.
Questa è una risposta errata. Se il modello riconosce soltanto una delle due
attività realmente contemporanee, la risposta completa è incompleta; le metriche
per singola attività permettono anche di vedere quale parte era corretta.

L'accuratezza dell'insieme completo richiede che tutte e sole le attività previste
coincidano con quelle attese. Precision, recall e F1 aiutano a distinguere falsi
allarmi e attività mancate. Vengono riportati anche il numero di esempi e le case
utilizzate. Senza etichette corrette non possiamo misurare l'accuratezza.

La valutazione delle case di test tiene separati i dati usati come riferimento.

Per il report: «È stata aggiunta una valutazione su dati etichettati, con misure
complessive e per attività e abitazione».

## 23. Confrontare quattro modi di riconoscere le attività

**Che cosa abbiamo cambiato.** Abbiamo aggiunto uno strumento sperimentale separato
che confronta quattro metodi sulle stesse registrazioni:

| Metodo | Che cosa utilizza |
| --- | --- |
| Regole con riferimenti | Il confronto microonde–forno e le regole configurate. |
| Regole dirette | Il precedente collegamento diretto tra proprietà e indizi delle attività. |
| Random Forest su ruoli condivisi | Un classificatore addestrato usando gli indizi già prodotti dal confronto con i riferimenti e la loro storia. |
| Random Forest su identità sorgenti | Un classificatore che usa i canali dei sensori/dispositivi identificati nella prima casa. |

Un **classificatore** è un programma addestrato su esempi con risposte corrette
per prevedere risposte su nuovi esempi. Una **baseline** è un metodo di confronto,
spesso più semplice o precedente, che serve a capire cosa aggiunge un cambiamento.

Per separare apprendimento e verifica, con 20 sessioni sorgente usiamo:

- 12 sessioni per addestrare i classificatori;
- 4 sessioni diverse per regolare i punteggi di probabilità;
- 4 sessioni diverse per il test finale sulla sorgente.

Le case diverse dalla sorgente vengono usate come test target. Le loro etichette
non entrano nell'addestramento. Si dividono sessioni complete, invece di mescolare
istanti vicini della stessa simulazione tra addestramento e test.

**Limite del confronto attuale.** Nel benchmark il target ha capacità fisiche che
la baseline diretta non converte automaticamente nei ruoli attesi. Il risultato
non dimostra che ogni metodo diretto dalle proprietà sarebbe peggiore.

Per il report: «È stato realizzato un ambiente di confronto tra regole e
classificatori, separando le sessioni di addestramento, validazione e test».

## 24. Trasformare le osservazioni in numeri utilizzabili dal classificatore

**Che cosa abbiamo cambiato.** Abbiamo costruito gli ingressi numerici del modello.
Questi ingressi sono chiamati **feature**, cioè caratteristiche misurabili.

Esempio: alle 18:45 possiamo descrivere quali indizi sono presenti, quanto sono
stati presenti negli ultimi minuti, quante volte sono cambiati e da quanto tempo
è stato osservato l'utilizzo di un dispositivo pertinente. Possiamo aggiungere
presenza, stato delle luci e, se disponibile, l'orario.

Nel classificatore su ruoli condivisi questi indizi provengono ancora dai
riferimenti configurati. Non stiamo usando direttamente capacità fisiche prive
di un collegamento iniziale al ruolo.

Gli ingressi usano solo informazioni disponibili fino a quell'istante. Non
includono la risposta corretta, la decisione finale delle regole o fatti futuri.
Alle 18:45 non possiamo usare come ingresso il fatto che l'attività finirà alle
19:10: sarebbe dare al modello un'informazione che in quel momento non possiede.

Per il report: «Sono state estratte caratteristiche temporali dalle osservazioni,
utilizzando esclusivamente informazioni disponibili al momento della previsione».

## 25. Addestrare il Random Forest nel percorso sperimentale

**Che cosa abbiamo cambiato.** Abbiamo aggiunto classificatori Random Forest,
composti da più alberi decisionali che combinano le proprie risposte.

Durante l'addestramento il modello riceve caratteristiche numeriche ed etichette
corrette delle sessioni sorgente. Impara a combinare gli indizi e la loro storia
per prevedere le attività. Può prevedere più attività contemporaneamente.

Il significato iniziale di alcuni indizi rimane configurato da noi: per esempio
il ruolo di cucina può essere già stato costruito confrontando un oggetto con il
forno. **Il modello attuale non dimostra quindi che il legame tra capacità fisiche
e attività sia stato imparato da zero.** Quella modifica è stata discussa come
possibilità futura e non è stata implementata.

Quando ci sono abbastanza esempi separati, regoliamo i punteggi usando la parte
sorgente di validazione: questa operazione si chiama **calibrazione**. Un punteggio
calibrato sulla sorgente non è automaticamente affidabile allo stesso modo in
un'altra casa. È anche possibile non emettere una decisione se il punteggio è
insufficiente: questa è l'**astensione**.

I modelli possono essere salvati e riutilizzati. Restano nello strumento
sperimentale: l'app durante la simulazione continua a usare le regole con riferimenti.

Per il report: «Sono stati implementati classificatori Random Forest su
rappresentazioni condivise e su identità sorgenti, con calibrazione separata quando applicabile».

## 26. Provare il sistema su scenari costruiti e ripetibili

**Che cosa abbiamo cambiato.** Abbiamo aggiunto una demo che genera registrazioni
artificiali, con una casa Oven/TV e una casa Microwave/PlayStation.

Lo script stabilisce cosa sta accadendo e produce le corrispondenti osservazioni:
per esempio cucina, intrattenimento prima della cucina, pasto con intrattenimento,
standby o produzione di calore con un asciugacapelli. Include anche situazioni in
cui il riconoscitore dovrebbe evitare una falsa attività.

Le etichette corrette derivano dall'intento dello script, non dalle risposte del
riconoscitore. Un numero iniziale, chiamato **seed**, permette di rigenerare gli
stessi dati. Il valore iniziale è 20 sessioni per casa.

Nella demo il riconoscimento di cucina richiede anche presenza rilevata dal PIR
in cucina. Questo requisito è un'opzione esplicita dell'esperimento e non deve
essere confuso con il comportamento più ampio di ogni scenario locale.

Questi dati sono **sintetici**: permettono di controllare il funzionamento in casi
costruiti, ma non misurano ancora l'accuratezza con persone e sensori reali.

Per il report: «È stato predisposto un benchmark sintetico riproducibile, con
dispositivi differenti tra sorgente e target e casi positivi e negativi».

## 27. Leggere, salvare e ripetere i risultati degli esperimenti

**Che cosa abbiamo cambiato.** La finestra **Source / target experiment…** esegue
il confronto e salva una cartella nuova con risultati, dati e modelli utilizzati.

Il report mostra quanto sono corrette le risposte e quali attività vengono perse
o riconosciute erroneamente. Mostra anche quante volte il modello decide e quante
si astiene. Nei report sperimentali questa proporzione di decisioni emesse è
chiamata **coverage**: è diversa dalla copertura delle capacità del Transfer report.
Le astensioni restano errori nell'accuratezza complessiva: non si possono eliminare
i casi difficili per presentare un risultato artificialmente alto.

La cartella conserva una copia dei dati, delle proprietà, delle impostazioni e
dei modelli usati. Questa copia conservata è detta *snapshot*. Un'impronta numerica,
detta *fingerprint*, permette di rilevare se quei contenuti sono cambiati.

Il **replay** riesegue la previsione usando i modelli e i dati conservati, senza
riaddestrarli e senza prendere nuove impostazioni dalla libreria corrente.
Serve a verificare che il risultato possa essere riprodotto.

Per il report: «Gli esperimenti producono report, predizioni e modelli salvati,
con controllo della configurazione utilizzata e possibilità di replay riproducibile».

## 28. Esempi, documentazione e verifiche del lavoro

**Che cosa abbiamo aggiunto.** Scenari dimostrativi, guide e test automatici per
controllare luci, salvataggio, riconoscimento, trasferimento e strumenti sperimentali.
Abbiamo anche confrontato il lavoro con i due PDF forniti e documentato quali
parti derivano dagli articoli e quali sono estensioni del progetto.

Occorre distinguere due verifiche:

- **Test del programma:** controllano che il codice faccia ciò che è previsto,
  per esempio che relaxing continui nel log quando termina l'evidenza del pasto.
- **Valutazione delle attività:** controlla se le risposte coincidono con etichette
  corrette nelle registrazioni utilizzate per l'esperimento.

Superare i test del programma non significa avere la stessa percentuale di
accuratezza nel riconoscimento delle attività reali.

Dopo la correzione del log sono stati verificati 633 test senza interfaccia e
23 test grafici, per 656 test distinti. Sono stati inoltre eseguiti esperimenti
sintetici da 20 sessioni per casa e verificato il replay delle predizioni.

Il [report aggiornato dopo la correzione del log](../recognition_library/reports/experiments/20261021T165030Z_0fc24fe6/report.html)
usa etichette concorrenti: eating, relaxing e la composita vengono considerate
separatamente. Il [report precedente](../recognition_library/reports/experiments/20261020T151513Z_862e282e/report.html)
usava un insieme di etichette diverso. I punteggi dei due report non sono un
confronto diretto prima/dopo a protocollo invariato.

Per il report: «Sono stati predisposti esempi, documentazione e test automatici,
oltre a prove sperimentali sintetiche; la validazione su dati reali resta da svolgere».

## Che cosa possiamo affermare nel report

Il sistema attuale utilizza proprietà e associazioni di riferimento configurate
per riconoscere attività attraverso osservazioni e regole. Questa conoscenza può
essere condivisa tra abitazioni. Sono stati aggiunti strumenti per spiegare le
decisioni, raccogliere etichette e confrontare metodi su sessioni separate.

La confidenza live è un punteggio euristico. La somiglianza tra oggetti non è
un'accuratezza misurata. I risultati sintetici non dimostrano superiorità generale
rispetto a un buon metodo diretto dalle proprietà e non garantiscono prestazioni
su case reali, sensori rumorosi o più occupanti.

L'apprendimento diretto del legame tra capacità fisiche e attività, senza passare
dai ruoli configurati dei riferimenti, **non è stato implementato**. Anche lo
spostamento dei moduli in un package `har/` è rimasto una proposta.

## Appendice: dettagli di implementazione originali

Questa appendice conserva l'inventario tecnico precedente. Puoi consultarla per
formule, parametri, campi dei file e controlli specifici, dopo aver letto la
spiegazione del punto corrispondente. I risultati riportati nel punto A28 sono
storici, precedenti alla correzione del log; l'aggiornamento segue nelle note.

### A16. Dettagli del punto 16: Dal riconoscimento diretto al trasferimento tramite riferimento

- La prima versione produceva evidenze direttamente dalle proprietà.
- Successivamente introdotto reference_transfer.py con associazioni esplicite
  tra un oggetto noto e un ruolo di evidenza.
- Il catalogo iniziale conosce Oven→cooking; non contiene Microwave→cooking.
- Analogamente, il ruolo relaxing di TV può essere trasferito a PlayStation.
- Prima di fornire un ruolo, un dispositivo o sensore osservato deve superare
  il confronto con un'associazione nota.
- Aggiunte proprietà del riferimento congelate, pesi delle proprietà rilevanti,
  proprietà obbligatorie, similarità minima, forza e peso dell'associazione.
- La similarità è direzionale: misura la copertura delle proprietà rilevanti del
  riferimento da parte dell'oggetto target; proprietà extra del target non penalizzano.
- Per ciascuna proprietà si usa min(1, valore_target / valore_riferimento), poi
  si calcola la media con i pesi delle proprietà rilevanti.
- Una proprietà obbligatoria assente o una similarità insufficiente rifiutano il match.
- La forza del match considera similarità, forza/peso dell'associazione e pesi
  generali dell'oggetto sorgente e target; l'osservazione aggiunge il proprio peso.
- La somiglianza generica, per esempio produrre calore, può essere esclusa attraverso
  proprietà essenziali relative alla preparazione del cibo.
- Nomi e tipi non saltano il controllo delle proprietà; i nomi servono a identificare
  oggetti e collegamenti, e l'identità distingue riferimento e sostituzione.
- Eliminare tutte le associazioni di un ruolo ne impedisce l'apporto osservato;
  il solo nome di una proprietà non aggira la mancanza del riferimento.
- Una mappa di associazioni esplicitamente vuota resta vuota dopo il caricamento.
- Trasferibili anche ruoli di sensori e ruoli personalizzati.

### A17. Dettagli del punto 17: Libreria condivisa tra abitazioni

- Creata recognition_library/ come archivio comune dell'installazione.
- Separati library.json, houses/, datasets/ e reports/.
- Aggiunti identificativo della libreria, versione dello schema e revisione del modello.
- Gestita la registrazione delle case con identità indipendente dal nome del file.
- Distinte registrazione/aggiornamento e registrazione come nuova casa.
- Aggiunta la scelta della casa di riferimento.
- Le associazioni note compatibili possono essere ancorate agli oggetti reali
  della casa sorgente; quelle non disponibili possono conservare il riferimento di catalogo.
- Aggiunti profili riutilizzabili di proprietà e regole condivise.
- I profili predefiniti compatibili possono essere ereditati; le personalizzazioni
  locali restano esplicite.
- Consentiti override per casa e pubblicazione del modello locale nella libreria.
- Collegamento e sincronizzazione delle case con il modello condiviso.
- Disconnessione con congelamento del modello effettivo e delle proprietà locali.
- Snapshot del modello e impronte per individuare cambiamenti di configurazione.
- Controlli delle revisioni per evitare sovrascritture su una versione superata.
- Scrittura atomica dei JSON e validazione dei profili prima di applicare aggiornamenti.
- Migrazione dei vecchi archivi verso le associazioni esplicite conservando le impostazioni.
- La directory è locale e comune alle case di questa installazione; non è un servizio cloud.

### A18. Dettagli del punto 18: Interfacce del riconoscimento e spiegazioni

- Aggiunta Scenario→Activity recognition… con risultati aggiornati periodicamente.
- Visualizzati supporto, confidenza, stato, evidenze, sensori e condizioni mancanti.
- Distinte attività riconosciute, inferite, incluse in un'altra attività o a bassa confidenza.
- Aggiunto l'editor di regole con pesi, soglie, peso dell'attività e finestre temporali.
- Consentita la definizione di regole personalizzate e il ritorno alla regola condivisa.
- Aggiunti editor Reference device rules… locale e Reference rules condiviso.
- Consentiti creazione, modifica e cancellazione delle associazioni di riferimento.
- Doppio clic sull'attività per leggere riferimento, target, proprietà confrontate,
  similarità, identificativo della regola, casa sorgente e sensore osservante.
- Conservate le spiegazioni dei riferimenti usati nelle sequenze temporali.
- Aggiunta Scenario→Shared recognition library… con schede Shared model,
  Transfer report, Accuracy / ground truth e Reference rules.
- Le modifiche condivise segnalano i precedenti risultati come da rivalutare.

### A19. Dettagli del punto 19: Confidenza, copertura e report di trasferibilità

- Separati supporto della regola, copertura delle evidenze e confidenza.
- Per le regole, confidenza = supporto × copertura; le attività inferite ricevono
  inoltre il fattore 0.85.
- Aggiunta una soglia minima di confidenza che permette di astenersi.
- Esplicitata la natura euristica e non calibrata della confidenza live.
- Aggiunto Transfer report con corrispondenze tra oggetti, copertura delle regole,
  condizioni mancanti e possibilità teorica di riconoscimento.
- Considerati anche pesi nulli e soglia di confidenza nella trasferibilità potenziale.
- Separata la copertura teorica dalla correttezza misurata su attività etichettate.

### A20. Dettagli del punto 20: Persistenza e compatibilità degli scenari

- Esteso il CSV con controllo delle luci e sezione Light Switches.
- Aggiunte sezioni Properties, Recognition Weights, Property Profiles,
  Shared Recognition e Activity Recognition.
- Salvati regole, tempi, associazioni di riferimento, identità della casa e snapshot
  del modello condiviso.
- I meter interni vengono ricostruiti dal dispositivo, evitando duplicati salvati
  come sensori indipendenti.
- Conservata la lettura di scenari precedenti con valori iniziali compatibili.
- Integrata la nuova configurazione nel caricamento, nella copia e nella pulizia
  dello scenario e dello stato della simulazione.

### A21. Dettagli del punto 21: Registrazione della verità di riferimento

- Raccolte osservazioni di sensori, dispositivi, porte, posizione, presenza e tempo.
- Aggiunto l'orario simulato esplicito per gli esperimenti che lo utilizzano.
- Aggiunta l'etichettatura manuale del frame corrente con una o più attività.
- Richiesta una scelta esplicita per Ground truth: no activity.
- Conservati i frame senza etichetta come contesto; non diventano esempi negativi.
- Salvataggio automatico nei dataset JSON con snapshot e identità della casa.
- Distinte partizioni Reference e Held-out test.
- Introdotti identificativi di run e di sessione, preservando l'identità di sessione
  attraverso cambiamenti del modello.
- Cambio di definizioni o partizione richiede una sessione indipendente.
- La registrazione etichetta il frame attuale, non automaticamente un intero intervallo;
  registrare di nuovo allo stesso istante sostituisce l'etichetta di quel frame.
- La traccia in memoria conserva fino a 4096 frame; le registrazioni uniscono il
  contesto disponibile al dataset già salvato.

### A22. Dettagli del punto 22: Valutazione contro etichette indipendenti

- Aggiunto il replay dei dataset etichettati con il modello da valutare.
- Esclusi i run di riferimento dalla valutazione held-out della libreria.
- Misurata l'accuratezza dell'insieme completo delle attività, oltre a precision,
  recall e F1 per attività e aggregati pertinenti.
- Aggiunti conteggi TP/FP/FN/TN, numero di frame, case e risultati per casa.
- In assenza di etichette i risultati non vengono sostituiti con accuratezze inventate.
- Le etichette reali sono inserite dall'utente o dallo script di scenario,
  e non copiate dalle predizioni del riconoscitore.

### A23. Dettagli del punto 23: Confronto sperimentale di quattro metodi

- Aggiunto har_experiment.py per confrontare regole con trasferimento, regole dirette,
  Random Forest su ruoli semantici e Random Forest sulle identità dei sensori/dispositivi sorgente.
- Le regole dirette sono mantenute come baseline del comportamento precedente.
- L'RF su identità usa uno schema fissato dalla sorgente; identità target non note
  hanno canali corrispondenti a zero.
- L'RF semantico usa ruoli e contesto condivisi, evitando la dipendenza dai nomi.
- Suddivisione delle sessioni Reference sorgente circa 60% training, 20% validation,
  20% test sorgente, per sessioni intere; minimo cinque sessioni sorgente.
- Eventuali run Test della sorgente restano test finali aggiuntivi.
- Le altre case con partizione Test costituiscono i test target.
- Training, vocabolario delle classi, schema raw e calibrazione usano dati sorgente.
- Le etichette target servono alla valutazione e non modificano l'addestramento.
- Rifiutati run duplicati, frammenti della stessa sessione, copie identiche delle
  osservazioni sorgente e identità incoerenti tra dataset e casa.
- Segnalata l'indipendenza non verificata dei dataset legacy privi di session_id.
- Rifiutate associazioni di riferimento provenienti da una casa target di test.
- Le classi assenti dal training sono segnalate come non supportate e contano come errori.

### A24. Dettagli del punto 24: Feature temporali e prevenzione della fuga di informazioni

- Aggiunto har_features.py per riprodurre causalmente ogni sessione.
- Separati i ruoli osservati prima delle inferenze composite scritte nelle regole.
- Per ruolo: ultimo valore, media pesata nel tempo, numero di cambiamenti e tempo
  dall'ultima evidenza positiva, con limite superiore esplicito.
- Aggiunti durata osservata della finestra e numero di eventi dei sensori.
- Contesto fisico: presenza, presenza nelle stanze pertinenti, peso sul letto,
  stato delle luci e potenza nulla dell'illuminazione.
- Orario opzionale codificato con seno/coseno, richiesto esplicitamente nei frame;
  il tempo trascorso non viene scambiato per l'ora del giorno.
- Esclusi dagli ingressi etichette, predizioni delle regole, confidenza e conclusioni
  composite come cooking_then_relaxing.
- Esclusi durata futura completa delle attività e confini derivati dalle etichette.
- Azzerato lo stato dinamico dello snapshot prima del replay, comprese le porte,
  per evitare che lo stato finale influenzi osservazioni precedenti.
- Congelati e applicati una volta profili e configurazione dell'esperimento.
- Memoria temporale nuova per ogni sessione; controllati timestamp duplicati/non finiti.

### A25. Dettagli del punto 25: Random Forest, calibrazione e astensione

- Aggiunto har_classifier.py con modelli multi-output: 128 alberi, profondità
  massima 12, minimo due campioni per foglia e seed riproducibile.
- Usato bilanciamento balanced_subsample e un processo di calcolo per la foresta.
- Separato il fitting sui dati training dalla calibrazione sui dati validation.
- Calibrazione sigmoidale per classe quando il training contiene entrambe le classi
  e la validation almeno cinque esempi positivi e cinque negativi.
- Negli altri casi i voti della foresta restano dichiaratamente non calibrati.
- Soglia positiva fissata a 0.5; controllo separato Minimum decision certainty
  per astenersi da decisioni insufficientemente certe.
- Applicato il cutoff richiesto anche alle decisioni delle regole, incluso l'idle.
- Esportati alberi e parametri sigmoidali in JSON, senza serializzare un estimator
  tramite pickle.
- Implementata predizione dagli alberi esportati e verificata la corrispondenza
  con le probabilità dell'estimator.
- Validati matrici, nomi, classi, valori finiti e struttura degli alberi serializzati.
- I modelli appresi appartengono allo strumento sperimentale; il riconoscimento
  live della simulazione continua a usare le regole con riferimenti.

### A26. Dettagli del punto 26: Benchmark sintetico indipendente

- Aggiunto har_benchmark.py con generazione deterministica tramite seed.
- Casa sorgente con Oven/TV, casa target con Microwave/PlayStation, nomi e geometrie differenti.
- Nel target usate capacità fisiche e proprietà extra senza associazioni dirette
  alle attività di cucina/intrattenimento.
- Aggiunto Hair Dryer come distrattore che produce calore ma non prepara cibo.
- Etichette definite dall'intento dello scenario, senza chiamare il riconoscitore
  per produrre le risposte corrette.
- Inclusi idle, standby con presenza, attività senza il contesto richiesto,
  intrattenimento prima della cucina, sequenza corretta e memoria scaduta.
- Variati tempi, durate e potenze mantenendo la riproducibilità.
- Default 20 sessioni per casa; il generatore accetta da 5 a 500.
- Generazione indipendente dai file del modello utente e dalla casa aperta.
- Campionamento più denso nelle fasi positive; metriche dichiaratamente pesate
  sui frame e influenzate dalla distribuzione di campionamento.
- Nel protocollo sperimentale aggiunto il requisito Kitchen PIR prima che la
  cucina entri in memoria; configurazione più ampia disponibile come opzione.
- La regola live standard conserva il comportamento più ampio se il requisito
  Kitchen PIR non viene abilitato.

### A27. Dettagli del punto 27: Metriche, report, interfaccia e riproducibilità degli esperimenti

- Aggiunto har_metrics.py con exact-match accuracy, micro/macro F1 e metriche per attività/casa.
- Aggiunti coverage, accuratezza sulle sole decisioni emesse e conteggi dei gruppi indipendenti.
- Le astensioni restano errori nel denominatore dell'accuratezza complessiva,
  anche nei frame idle; le predizioni parziali restano nei conteggi per attività.
- Le attività target sconosciute contribuiscono ai falsi negativi.
- Brier score disponibile sulle probabilità delle classi note, con ambito dichiarato.
- F1 non applicabile nei casi solo-idle non viene trasformato in un punteggio perfetto.
- Aggiunta Scenario→Source / target experiment… e un collegamento dalla libreria.
- Scelta fra demo sintetica e dataset registrati; controlli per seed, finestra,
  numero di sessioni demo, certezza minima, Kitchen PIR e orario.
- Calcolo in un worker separato; gli aggiornamenti Tk avvengono sul thread UI.
- Gestiti indicatori di lavoro, disabilitazione temporanea dei comandi, errori,
  risultati precedenti, chiusura durante il calcolo e avvii duplicati.
- Tabella di confronto e dettagli su split, attività, case, calibrazione e limiti.
- Comando per copiare il percorso dei risultati.
- Aggiunta CLI con --demo, --library e --replay.
- Creata una directory nuova per ogni esecuzione; rifiutata la sovrascrittura di risultati.
- Esportati report HTML/Markdown/JSON, comparison.csv, per_activity.csv,
  predictions.json, features.json, experiment.json e models.json.
- Congelati osservazioni, etichette, impostazioni, profili, modelli e schemi.
- Calcolate impronte di dati, modelli/configurazione, feature e codice di inferenza.
- Replay senza riaddestramento o letture dalla libreria live; modifiche non
  corrispondenti alle impronte vengono rifiutate.

### A28. Dettagli del punto 28: Esempi, documentazione, integrazione e verifiche

- Aggiunti esempi Oven/TV, Microwave/PlayStation e Bedroom/Bathroom.
- Aggiunti oven_reference.csv e microwave_target.csv con proprietà fisiche,
  associazione nota solo al forno e nessuna proprietà cooking necessaria sul microonde.
- Documentati percorso utente, configurazione, etichettatura, risultati e replay.
- Analizzati i due PDF forniti e scritto docs/research_alignment.md per distinguere
  quanto descritto negli articoli dalle estensioni richieste.
- Aggiunta docs/source_target_experiment.md e aggiornati README e guida della libreria.
- Registrati i moduli Python nuovi nella configurazione del pacchetto.
- Esclusi i risultati locali degli esperimenti dal versionamento Git.
- Aggiunti test per illuminazione, controllo, interruttori, potenza stanza,
  cronologia, persistenza e dialoghi di salvataggio.
- Aggiunti test per proprietà, attività, sequenze, sonno, libreria condivisa,
  associazioni esplicite, spiegazioni e trasferimento.
- Aggiunti test per benchmark, causalità delle feature, separazione dei dati,
  classificatori, calibrazione, metriche, esportazione e replay.
- Aggiunti test Tk reali per le nuove finestre e il collegamento ai backend.
- Verifica del 21 ottobre 2026: 647 test individuati; 626 eseguiti con successo
  nel run senza GUI, 21 test GUI esclusi perché opt-in.
- I 21 test GUI erano stati eseguiti con successo nella verifica del 20 ottobre.
- Eseguito l'esperimento sintetico con 20 sessioni per casa: 12 training, 4 validation,
  4 test sorgente e 20 test target, con 4549 frame target etichettati.
- Nel run salvato: exact accuracy target 99.56% per le regole con trasferimento,
  25.65% per le regole dirette, 100% per RF semantico e 25.65% per RF sulle identità.
- Verificato il replay con predizioni identiche e corrispondenza delle impronte.

### Note tecniche, aggiornamenti e verifiche precedenti

Aggiornamento successivo alla verifica iniziale: è stata corretta la soppressione
di eating/relaxing da parte della composita. Le attività di base conservano ora
intervalli indipendenti e la composita richiede entrambe, con confidenza limitata
da quella dei componenti. Il log e la scheda Activity mostrano e aggiornano anche
gli intervalli in corso; il CSV li esporta con fine vuota senza chiuderli. Il
benchmark registra ora le due etichette di base insieme alla composita. I numeri
del benchmark e dei test riportati sopra descrivono la verifica precedente alla
correzione, non una nuova misura del comportamento aggiornato.

Verifica della correzione: 633 test senza GUI e 23 test GUI superati (656 test
distinti complessivi). Ripetuto il benchmark con 20 sessioni per casa e con le
etichette concorrenti aggiornate; il nuovo
[report](../recognition_library/reports/experiments/20261021T165030Z_0fc24fe6/report.html)
descrive questo protocollo. I punteggi prima/dopo non sono direttamente
confrontabili perché è cambiato l'insieme delle etichette attese.

- Le associazioni note, le proprietà e le regole sono conoscenza configurata;
  il Random Forest impara dai dati etichettati nel percorso sperimentale.
- Alcuni profili standard usano ancora proprietà con nomi di attività. Gli esempi
  fisici e il benchmark mostrano il trasferimento anche senza quei nomi nel target.
- Il matcher confronta chiavi normalizzate e pesi: non è un reasoner ontologico
  generale e non comprende automaticamente sinonimi o gerarchie arbitrari.
- Confidenza euristica live, similarità delle proprietà e accuratezza misurata
  devono essere presentate come quantità diverse.
- La calibrazione sul validation sorgente non garantisce calibrazione nelle altre case.
- I risultati del benchmark sono sintetici: non stabiliscono accuratezza su persone,
  case reali, sensori rumorosi/mancanti o più occupanti.
- Il confronto è tra le quattro implementazioni definite; non dimostra che ogni
  possibile metodo diretto o basato sui dati grezzi sarebbe inferiore.
- L'inferenza del pasto dopo la cucina è un'ipotesi comportamentale esplicita.
- La riorganizzazione dei moduli in un package har/ è stata consigliata, ma non
  è stata eseguita: i moduli har_*.py restano nella radice del progetto.

## Aggiornamento del 22 ottobre 2026: editor semplificato dei riferimenti

È stata semplificata soltanto la finestra aperta da **Activity recognition… >
Reference device rules…**. Si scelgono il dispositivo sorgente noto e il
destinatario della casa corrente. Il confronto delle proprietà avviene subito:
la tendina permette di selezionare una o più attività compatibili. Il cambio di
sorgente o destinatario ricalcola le opzioni e azzera la selezione precedente.

I sorgenti possono provenire dai riferimenti memorizzati, dal catalogo condiviso
(o da quello predefinito se la libreria non esiste) o da dispositivi effettivamente
presenti nelle case registrate, purché abbiano un'associazione nota. Il catalogo dei
sorgenti resta disponibile anche quando la casa contiene inizialmente solo il
riferimento del forno e dopo il salvataggio di una prima associazione. Soltanto
le attività selezionabili dipendono dalla compatibilità della coppia; si possono
aggiungere forno/microonde e poi TV/PlayStation nella stessa finestra. Consultare
il catalogo non attiva automaticamente altre associazioni nella casa. Dopo il
salvataggio sorgente e destinatario vengono deselezionati, mantenendo disponibili
tutte le scelte per aggiungere una nuova coppia. È stata rimossa la successiva
gestione aggiuntiva di raggruppamento dei profili; il confronto usa il singolo
riferimento scelto e le regole di proprietà già esistenti.

Corretto anche il riferimento Oven del catalogo condiviso: oltre all'associazione
precedente con `cooking`, contiene le capacità fisiche `heats_food` e
`food_preparation` e un'associazione che le confronta con pesi 0,6 e 0,4,
richiedendo `food_preparation`. Entrambe le associazioni descrivono lo stesso
forno e compaiono nella stessa voce **Shared catalogue: Oven (Oven)**. Il matcher
non è stato modificato. Il catalogo locale è stato aggiornato alla revisione 4;
il caso dello screenshot è stato verificato scegliendo proprio questa voce.

Il salvataggio aggiunge i riferimenti scelti alla configurazione della
casa; non assegna nuove proprietà-attività al destinatario e non rimuove le altre
associazioni. I riferimenti restano utilizzabili dagli altri dispositivi
compatibili. Occorre salvare lo scenario CSV per conservare le modifiche.

La compatibilità viene ricontrollata al salvataggio. Sono rispettati proprietà
obbligatorie, pesi e soglie di similarità; la compatibilità non sostituisce i
sensori o le condizioni di contesto durante il riconoscimento. Le attività
composte continuano a dipendere dalle attività componenti e dalla sequenza.

Il pulsante **Advanced reference rules…** apre l'editor precedente, con ID,
proprietà, pesi, soglie e cancellazione. La finestra principale Activity
recognition e il percorso sperimentale ML non sono stati riorganizzati.

La creazione occupa ora circa il 75% della finestra. Il pannello destro
**Current connections** elenca i riferimenti dei dispositivi, il ruolo e i
destinatari compatibili. **Remove selected** elimina i riferimenti selezionati
soltanto dalla configurazione della casa corrente, per tutti i dispositivi che
li usano. La rimozione viene conservata salvando lo scenario CSV; il catalogo
condiviso e le altre case restano invariati. Verificate disposizione, rimozione,
effetto sul riconoscimento e persistenza dopo riapertura con i test GUI mirati.

Verifica mirata aggiornata: 34 test sui riferimenti e 3 test GUI superati, inclusi
filtro preventivo, selezioni diventate incompatibili, accesso all'editor avanzato
e aggiunta consecutiva di forno/microonde e TV/PlayStation partendo dall'esempio
che inizialmente contiene soltanto l'associazione del forno, con azzeramento
della coppia dopo ciascun salvataggio.

## Aggiornamento del 22 ottobre 2026: libreria condivisa semplificata

**Scenario > Shared recognition library…** apre ora una vista dedicata alle case:
registrazione/aggiornamento della casa corrente, registrazione con nuova identità,
collegamento al modello condiviso e scelta della casa di riferimento. Una tabella
mostra le case registrate, i ruoli e il numero di dispositivi e sensori. Si può
scegliere come riferimento anche una casa registrata diversa da quella aperta,
senza cambiare l'identità della casa corrente.

**Reference device rules…** apre l'editor di associazione già esistente.
**Advanced…** conserva la finestra completa precedente, con profili, regole,
confronto di trasferibilità ed etichettatura/valutazione. Alla chiusura della
finestra avanzata la vista semplice aggiorna nomi e riferimento.

Un messaggio statico chiarisce che scegliere il riferimento seleziona gli oggetti
sorgente per le associazioni condivise esistenti, senza pubblicare le sue regole
locali. La pubblicazione resta esplicita nella vista avanzata. Sono ricordati
anche la precedenza delle configurazioni locali e il salvataggio dello scenario
per conservarne identità e collegamenti. Il motore di riconoscimento e il confronto
delle proprietà non sono stati modificati.

Verificati con test GUI il flusso a due case, la scelta del riferimento, la
conservazione delle regole locali, l'accesso alle associazioni e alla vista
avanzata, la sincronizzazione dei nomi e la visibilità dei comandi a 880 × 600.
Restano superati i controlli su trasferimento, valutazione e salvataggio/riapertura.

## Riferimenti locali

- [Illuminazione](../lighting.py)
- [Potenza delle stanze](../room_recognition.py)
- [Proprietà](../device_properties.py)
- [Riconoscimento](../activity_recognition.py)
- [Trasferimento tramite riferimenti](../reference_transfer.py)
- [Libreria condivisa](../recognition_library.py)
- [Etichettatura e valutazione delle regole](../recognition_evaluation.py)
- [Pipeline sperimentale](../har_experiment.py)
- [Feature](../har_features.py)
- [Classificatore](../har_classifier.py)
- [Metriche](../har_metrics.py)
- [Benchmark](../har_benchmark.py)
- [Guida sperimentale](source_target_experiment.md)
- [Allineamento con gli articoli](research_alignment.md)
- [Report sintetico eseguito](../recognition_library/reports/experiments/20261020T151513Z_862e282e/report.html)
