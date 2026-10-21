# Proposta històrica: regles configurables per columna

> Document de disseny anterior a la implementació. Les expressions «proposat» i «sense implementació» descriuen aquella fase, no l’estat actual. El comportament vigent és a [column-rules-spec.md](column-rules-spec.md), [validation-spec.md](validation-spec.md) i [usability-spec.md](usability-spec.md).

## Separació de responsabilitats

Mantenir el motor estructural independent d’Odoo. Afegir posteriorment un motor de contingut Python que rebi especificacions declaratives de regles i emeti les mateixes incidències que l’informe actual. L’adaptador Odoo transformarà la configuració en aquestes especificacions; el motor no consultarà l’ORM ni executarà codi introduït per l’usuari.

L’extensió actual `_validation_rules()` pot adaptar les regles sense estat. Per a unicitat, cal un context nou per cada execució, amb preparació de capçaleres, validació de registres i finalització. No guardar conjunts de valors entre peticions ni al nivell de classe. Aquesta evolució de l’API només es proposa, no s’implementa ara.

## Configuració a Odoo

- Perfil reutilitzable amb nom, propietari i línies de configuració per columna, independent de qualsevol model de negoci.
- Cada línia identifica una columna per nom de capçalera i defineix `required`, `unique`, tipus `text` o `number`, i un mínim opcional emmagatzemat com a text decimal validat. No usar Float per guardar un llindar exacte.
- Selecció de perfil a l’assistent i revisió de correspondències amb les capçaleres del CSV. Configuració privada per defecte; qualsevol compartició requereix permisos explícits.
- Si una columna configurada no existeix, o el nom és ambigu per capçaleres duplicades, mostrar una incidència de configuració. No validar silenciosament una altra columna.
- Rebutjar configuracions repetides per a una mateixa columna. Un mínim només és admissible per a tipus numèric. Una execució utilitza una còpia immutable de les opcions per evitar canvis a mig procés.

## Semàntica proposada

| Regla | Comportament |
| --- | --- |
| Obligatòria | Rebutja cadena buida o amb només espais, segons una opció explícita de retallar espais per comparar. |
| Única | Rebutja la segona aparició i les següents; informa de la línia de la primera aparició. Valors buits opcionals s’ignoren; obligatorietat els tracta separadament. |
| Numèrica | Converteix amb `decimal.Decimal`, amb separador decimal explícit (`.` o `,`); rebutja NaN, infinits i sintaxi no admesa. Sense detecció regional ni separadors de milers implícits. |
| Mínim | Compara el Decimal vàlid amb un mínim inclusiu (`valor >= mínim`). Un error numèric no genera també un error de mínim. |

Per defecte, unicitat textual sensible a majúscules i comparació amb espais als extrems retallats; el perfil mostra aquesta política. En columnes numèriques, comparar valors Decimal: `1` i `1.0` són iguals. No tractar identificadors amb zeros inicials com a números llevat que la columna estigui configurada així.

Ordre per cel·la: obligatorietat, conversió numèrica, mínim, unicitat del valor vàlid. Una cel·la buida opcional no genera errors de tipus o mínim. Cap normalització altera les dades originals.

## Execució, límits i informes

Executar regles de contingut només amb capçalera resolta i registres amb amplada correcta; les files malformades conserven l’error estructural. Una validació estructural interrompuda també marca incompleta la validació de contingut. El total de registres continua significant registres llegits, no registres sense errors.

El context d’unicitat conserva només claus de comparació i primera línia per columna. Proposo inicialment un màxim de 20 columnes amb unicitat, 100.000 claus totals i 32 MiB de contingut de claus; les mesures de càrrega decidiran els valors finals i el límit de memòria del procés continuarà essent necessari. Si s’esgota un pressupost, informar que l’anàlisi és incompleta; no afirmar que les files restants són úniques. No basar la detecció només en hashes amb risc de col·lisió.

Reutilitzar el límit global de 1.000 detalls, tot continuant el recompte quan sigui possible. Codis estables: `REQUIRED`, `DUPLICATE_VALUE`, `INVALID_NUMBER`, `BELOW_MINIMUM`, `RULE_COLUMN_MISSING` i `RULE_CONFIGURATION`. Afegir metadades estructurades, inclosa primera línia del duplicat, sense necessitat d’incloure valors sensibles a l’informe o als logs. Mantenir la protecció contra fórmules en l’exportació CSV.

## Proves abans d’activar la fase

Proves Python de buits, espais, sensibilitat a majúscules, duplicats, zeros inicials, decimals exactes, negatius, límit inclusiu, separador decimal, NaN/infinits i independència entre execucions. Proves de capçaleres absents o ambigües, files estructuralment incorrectes, combinació de regles, límits de memòria i informes parcials. Proves Odoo de permisos dels perfils, selecció i correspondència de columnes. Les fixtures han d’incloure CSV de contactes, finances i dades arbitràries; cap regla dependrà de productes.
