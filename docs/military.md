# Military records: what to look for and where

A conscription entry (fiche matricule, France) can give the parents, the place of residence, a physical description, the trade, the units, the campaigns, wounds and decorations of a man. This page lists the public sources and how to record an entry as a `gwt-acts` card. It copies no data from any of them and it automates nothing.

## Card for a matricule entry

`gwt-acts check` accepts `type = matricule`. The card needs the usual columns (see [gwt-acts](acts.md)) and two more: `classe` (the conscription class) and `matricule_no` (the number in the register). `date_iso` is the birth date the entry gives. `unit`, `campaigns` and `decorations` are free text columns that `gwt-acts` does not read; they are for the person who writes the note. A matricule card is matched to a person in the tree like any other card, and it is compared with no event: the entry is not a birth act.

Read the identity lines on a zoom before you write a parent. The same rule as for any act applies: state `verificado-zoom` needs the zoom, and a name with "(?)" cannot be `verificado-zoom`.

## Where the sources are

Each line says what the source holds according to the page linked, and what to check before you rely on it. None was tested for every department or year.

- **Departmental archives, conscription registers.** The register of the class a man belonged to is in the archives of the department of his recruitment office. A 2014 article reports that Loir-et-Cher put its registers for the classes 1867 to 1921 online ([RFG](https://www.rfgenealogie.com/infos/loir-et-cher-les-registres-matricules-sont-en-ligne)). That is eleven years old: check the archive's own site for what is online today. Later classes are generally not published; do not assume a class is online because an earlier one is.
- **Légion d'honneur, base Léonore.** The [Léonore database](https://www.leonore.archives-nationales.culture.gouv.fr/ui/) lists the files of members of the Légion d'honneur. The Archives nationales' [help page](https://www.siv.archives-nationales.culture.gouv.fr/siv/cms/content/helpGuide.action?preview=false&searchString=&typeSearch=&uuid=34983672-e919-4a6e-a1a4-db9198394419&version=3) says it covers people who died before 1977. A man with no decoration is not in it, so no result is not an error.
- **Mémoire des hommes.** The Ministry of the Armed Forces' portal has a [global search of its nominal databases](https://www.memoiredeshommes.defense.gouv.fr/recherche-globale/rechercher-dans-les-bases-nominatives). It is the place for a man who died in a war, and for other nominal bases it holds. Check each base's own page for what it covers.
- **Médaille militaire.** The [Grande Chancellerie's archive](https://archives.legiondhonneur.fr/informations-pratiques/nous-contacter/vous-recherchez-une-decoree-de-la-medaille-militaire-vivant-ou-decede) has a page on how to search for a holder, living or dead. It says something about files closed less than 50 years ago: read it before you ask for anything.
- **Spain.** Search the Ministerio de Defensa's heritage portal (for example the inventory of personal files of soldados trabajadores, BDST) and the national archives portal PARES, and check what each one holds before you rely on it. An index line with a name does not identify a man. Do not attribute a file to a person in the tree until the file gives a birth date and parents that match.

## Pace and terms

Read each site's current terms. Many departmental sites use a proof-of-work page or a captcha; the toolkit never solves one. Open such a site in a browser, at human pace, and type the card by hand. Write to nobody for a file about a person who may be alive, and search for the dead only when you have confirmed they are dead.

## What a matricule card does not prove

A decoration listed in a register is what the clerk wrote. A missing decoration may mean none was awarded, or that the register was not updated. Say "the entry records no decoration", never "he had none".
