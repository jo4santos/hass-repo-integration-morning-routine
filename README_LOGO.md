# Logotipo da Integração Morning Routine

## Ficheiros Criados

- `logo.svg` - Logotipo detalhado com sol, checklist, estrela e corações
- `icon.svg` - Ícone simplificado focado na checklist com estrela

## Conversão para PNG (Necessária)

O Home Assistant requer ficheiros PNG. Escolhe uma das opções:

### Opção 1: Conversor Online (Mais Fácil)
1. Acede a https://cloudconvert.com/svg-to-png
2. Faz upload de `logo.svg` e converte para PNG (256x256)
3. Faz upload de `icon.svg` e converte para PNG (256x256)
4. Guarda como `logo.png` e `icon.png` nesta pasta

### Opção 2: Comando (Se tiveres ImageMagick instalado)
```bash
convert -background none -density 300 -resize 256x256 logo.svg logo.png
convert -background none -density 300 -resize 256x256 icon.svg icon.png
```

### Opção 3: Inkscape (Se estiver instalado)
```bash
inkscape logo.svg --export-type=png --export-width=256 --export-filename=logo.png
inkscape icon.svg --export-type=png --export-width=256 --export-filename=icon.png
```

## Descrição do Design

### Logo (logo.svg/logo.png)
- **Fundo**: Gradiente laranja (#FFB74D → #FF9800) representando manhã energética
- **Sol**: No canto superior esquerdo com raios, simbolizando o nascer do sol
- **Checklist**: Papel branco com 3 checkboxes (2 completos, 1 pendente)
- **Estrela**: Dourada no topo, representando gamificação e recompensas
- **Corações**: Rosa no canto inferior, simbolizando amor e carinho por crianças

### Icon (icon.svg/icon.png)
- Versão simplificada para melhor visualização em tamanhos pequenos
- Fundo laranja circular
- Checklist com 3 items completos (checkmarks verdes)
- Estrela dourada no topo
- Design mais limpo e focado

## Cores Usadas

- Laranja: #FF9800, #FFB74D (energia matinal)
- Verde: #4CAF50 (conclusão, sucesso)
- Dourado: #FFC107 (recompensas, estrelas)
- Rosa: #E91E63 (carinho, crianças)
- Branco: #FFFFFF (clareza, limpeza)

## Uso no Home Assistant

Após converter para PNG, estes ficheiros serão automaticamente usados pelo Home Assistant:
- `logo.png` - Mostrado na página de integrações
- `icon.png` - Usado em cards e UI elements

