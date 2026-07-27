# Audit pilote — interpellations de Vevey

Périmètre : publications classées comme `Interpellation` en 2025 et 2026 sur
le site du Conseil communal de Vevey.

## Résultat du 26 juillet 2026

- 152 occurrences parcourues sur les 6 pages du filtre Interpellation ;
- 46 occurrences appartiennent à 2025–2026 (28 en 2025, 18 en 2026) ;
- 45 téléchargements PDF valides ;
- 43 contenus PDF uniques après calcul SHA-256 ;
- 1 groupe de trois occurrences contient exactement le même PDF ;
- aucun des 43 PDF uniques ne nécessite d'OCR selon le seuil du pilote ;
- les PDF contiennent entre 1 et 8 pages, avec environ 3 398 caractères
  extraits en moyenne.

## Audit avant indexation

Décision automatique : `ready_with_warnings`.

- 0 anomalie bloquante ;
- 28 avertissements répartis sur 15 documents ;
- 14 documents sans auteur dans la liste officielle ;
- 12 documents dont le site fournit le nom de fichier comme titre ;
- 1 document de huit pages contient trois pages sans texte, mais les cinq
  autres pages fournissent plus de 14 000 caractères ;
- 42 PDF sont des objets politiques déposés ;
- 1 PDF est une réponse municipale (`2026/RI03`) malgré son classement sous
  le filtre Interpellation du site.

Les auteurs et titres manquants devront être enrichis depuis le contenu des
PDF avant de générer les embeddings. Ils ne bloquent pas l'extraction ni la
future recherche sémantique, mais leur correction améliorera les filtres et
l'affichage des sources.

Le lien `download.asp?d=6176` renvoie actuellement `file error` au lieu d'un
PDF. La même publication, `Sauvons le LIDO`, est toutefois disponible par le
lien plus récent `download.asp?d=6188`. Le pilote conserve l'anomalie dans
`inventory.json` et associe le lien cassé au document équivalent disponible.

## Décision

La source est techniquement exploitable, mais l'enrichissement des 15
documents signalés doit précéder l'indexation. Il restera ensuite à
transformer les 43 PDF canoniques en textes/chunks, ajouter `city=Vevey` aux
entrées de recherche et collecter séparément les autres réponses publiées
par Vevey sous le type `Annexe` avec des références `RI`.
