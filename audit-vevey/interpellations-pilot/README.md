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

Le lien `download.asp?d=6176` renvoie actuellement `file error` au lieu d'un
PDF. La même publication, `Sauvons le LIDO`, est toutefois disponible par le
lien plus récent `download.asp?d=6188`. Le pilote conserve l'anomalie dans
`inventory.json` et associe le lien cassé au document équivalent disponible.

## Décision

La source est exploitable pour la suite du pilote. Avant indexation, il reste
à transformer les 43 PDF canoniques en textes/chunks, ajouter `city=Vevey`
aux entrées de recherche et traiter séparément les réponses, publiées par
Vevey sous le type `Annexe` avec des références `RI`.
