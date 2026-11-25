# Prévention Maladies - vidéo short

Export automatique en **MP4 (9:16, 1080x1920, 30 fps)** des vidéos courtes du projet « Maladies en Short ».

## Comment ça marche

1. Un storyboard JSON décrit la vidéo : `storyboards/<slug>.json` (scènes, minutage, textes, couleurs d'accent, images).
2. `generate.py` fabrique, image par image (Pillow) :
   - les frames JPG (Ken Burns sur l'illustration de fond, dégradé sombre, titre coloré, boîtes de sous-titres synchronisées, mention « ne remplace pas un avis médical ») ;
   - la piste audio WAV (nappe lo-fi 4 accords, basse, kick feutré, charley, marimba : 72 BPM, miroir du moteur Web Audio du canvas).
3. La GitHub Action `.github/workflows/export.yml` assemble frames + audio avec ffmpeg (h264 + AAC) et publie le MP4 en **release** du dépôt.

## Produire une nouvelle vidéo

- Ajouter un storyboard : `storyboards/<slug>.json` (mêmes champs que `rhume-short.json`).
- Ajouter les images de fond en base64 : `assets/images/<id>.jpg.b64`.
- Le push déclenche l'export ; le MP4 arrive dans les releases.

## Notes

- La narration vocale (voiceSynthesis du navigateur) et l'enregistrement d'écran restent la méthode pour la voix off (voir dossier `03-audio` du projet).
- `python generate.py --storyboard storyboards/<slug>.json --out out --smoke` pour un rendu de contrôle rapide (quelques frames + audio court).
