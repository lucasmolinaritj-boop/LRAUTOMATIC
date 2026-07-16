# LRAutomatic

Automação local do Adobe Lightroom Classic por **aplicativo desktop + CLI + API HTTP + plugin Lua**.

O Lightroom permanece aberto com o plugin instalado. O serviço Python recebe tarefas, grava uma fila persistente em disco e o plugin importa as fotos no catálogo. Uma tarefa pode conter várias pastas.

## Recursos atuais

- Aplicativo desktop com criação de catálogo e ZIP de diagnóstico.
- Criação de catálogo pelo app a partir de um catálogo-modelo oficial vazio.
- Abertura automática do novo catálogo no Lightroom.
- Importação de múltiplas pastas em um comando.
- API local com autenticação por chave.
- Fila persistente e retomada.
- Coleção por pasta e conjunto de coleções opcional.
- Detecção de fotos já importadas.
- Solicitação de Smart Previews pelo próprio Lightroom após a importação.
- Logs do serviço, plugin e automação de Smart Previews.
- ZIP sanitizado para depuração, sem incluir a chave da API.

## Requisitos

- Windows 10/11.
- Adobe Lightroom Classic.
- Python 3.11 ou superior.

## Instalação

Execute:

```bat
instalar.bat
```

Depois configure `config.json`:

```json
{
  "host": "127.0.0.1",
  "port": 45821,
  "api_key": "SUA-CHAVE",
  "data_dir": "%LOCALAPPDATA%\\LRAutomatic",
  "catalog_template": "D:\\Modelos\\CatalogoModelo.lrcat",
  "catalog_output_root": "D:\\Catalogos",
  "lightroom_executable": "C:\\Program Files\\Adobe\\Adobe Lightroom Classic\\Lightroom.exe"
}
```

O catálogo-modelo pode ser colocado depois. Ele deve ser um catálogo vazio criado uma única vez pelo próprio Lightroom. A partir daí, o app cria e nomeia todos os novos catálogos automaticamente.

## Abrir o aplicativo

```bat
abrir_app.bat
```

No aplicativo existem os botões:

- **Criar e abrir no Lightroom**
- **Gerar ZIP de diagnóstico**

## Instalar o plugin

1. Abra **Arquivo > Gerenciador de plug-ins** no Lightroom Classic.
2. Clique em **Adicionar**.
3. Selecione `lightroom_plugin\LRAutomatic.lrplugin`.
4. Reinicie o Lightroom após atualizar arquivos do plugin.

## Importar múltiplas pastas

```bat
lrautomatic import ^
  --source "E:\Imovel_101|Casa 101" ^
  --source "E:\Imovel_102|Casa 102" ^
  --collection-set "Trabalhos Julho 2026" ^
  --recursive ^
  --smart-previews
```

Por JSON:

```bat
lrautomatic import --job examples\import-job.json
```

Consultar tarefas:

```bat
lrautomatic jobs
lrautomatic status JOB_ID
```

## Criar catálogo pelo CMD

```bat
lrautomatic catalog-create --name "Imovel 582"
```

O app cria uma pasta própria para o trabalho, copia o catálogo-modelo para o nome definitivo e abre o Lightroom apontando diretamente para esse catálogo.

## Smart Previews

Quando `--smart-previews` estiver ativo, o plugin:

1. importa as fotos usando o SDK;
2. seleciona somente as fotos recém-importadas;
3. chama um script PowerShell;
4. o script ativa o Lightroom e solicita **Criar visualizações inteligentes** pela interface;
5. o próprio Lightroom gera os arquivos oficiais.

Como a Adobe não expõe essa operação no SDK público, essa etapa usa automação da interface. Ela pode precisar de ajuste conforme idioma e versão. Todos os resultados ficam em:

```text
%LOCALAPPDATA%\LRAutomatic\logs\smart-previews.log
```

A sequência de teclas pode ser sobrescrita pela variável:

```bat
set LRAUTOMATIC_SMART_PREVIEW_KEYS=SEQUENCIA
```

## ZIP de diagnóstico

Pelo aplicativo, clique em **Gerar ZIP de diagnóstico**.

Ou pelo CMD:

```bat
lrautomatic diagnostic-zip --output "%USERPROFILE%\Desktop"
```

O ZIP inclui:

- informações do Windows e Python;
- dependências instaladas;
- processos em execução;
- configuração com segredos removidos;
- até 100 arquivos recentes de tarefas, respostas, controles e logs;
- log do plugin;
- log da geração de Smart Previews.

Ele não inclui suas fotos, catálogo `.lrcat` ou chave da API.

## API

- `GET /health`
- `POST /api/v1/import-jobs`
- `GET /api/v1/import-jobs`
- `GET /api/v1/import-jobs/{job_id}`
- `POST /api/v1/import-jobs/{job_id}/cancel`

A API usa `Authorization: Bearer SUA_CHAVE`.

## Observações importantes

- O Lightroom aceita apenas um catálogo aberto por vez.
- A criação do `.lrcat` usa um catálogo-modelo oficial porque o SDK público não cria um catálogo vazio do zero.
- Smart Preview é gerado pelo Lightroom, mas o acionamento é feito por automação de interface e deve ser validado na instalação real.
- O projeto não escreve diretamente no SQLite interno do catálogo.
