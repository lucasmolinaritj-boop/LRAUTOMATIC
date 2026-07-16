# LRAutomatic

Automação local do Adobe Lightroom Classic por **CLI + API HTTP + plugin Lua**.

O Lightroom permanece aberto com o plugin instalado. O serviço Python recebe tarefas, grava uma fila persistente em disco e o plugin importa as fotos no catálogo atualmente aberto. Uma única tarefa pode conter várias pastas.

> Estado: MVP inicial. A importação por `catalog:addPhoto()` está implementada. A criação de Smart Previews e a troca de catálogo não possuem uma API pública estável no Lightroom SDK e, por isso, aparecem como capacidades pendentes, sem fingir que foram executadas.

## Recursos

- Importação de múltiplas pastas em um comando.
- API local com autenticação por chave.
- Fila persistente e retomada após reiniciar o serviço.
- Uma coleção por pasta, opcional.
- Coleção-conjunto por tarefa, opcional.
- Busca recursiva.
- Filtro de extensões RAW/JPEG/TIFF/HEIC/DNG.
- Status geral e detalhado por pasta.
- Plugin Lightroom Classic em Lua.
- Preparação para catálogo-modelo, que pode ser configurado depois.

## Requisitos

- Windows 10/11.
- Adobe Lightroom Classic.
- Python 3.11 ou superior.

## Instalação do serviço

```bat
py -m venv .venv
.venv\Scripts\activate
pip install -e .
copy config.example.json config.json
```

Edite `config.json` e troque `api_key`.

Inicie o serviço:

```bat
lrautomatic serve
```

A API ficará em `http://127.0.0.1:45821`.

## Instalação do plugin

1. Copie a pasta `lightroom_plugin/LRAutomatic.lrplugin` para um local permanente.
2. No Lightroom Classic, abra **Arquivo > Gerenciador de plug-ins**.
3. Clique em **Adicionar** e selecione `LRAutomatic.lrplugin`.
4. Mantenha o Lightroom aberto no catálogo que receberá as fotos.

Por padrão, serviço e plugin usam:

```text
%LOCALAPPDATA%\LRAutomatic
```

## Importar múltiplas pastas pelo CMD

```bat
lrautomatic import ^
  --source "E:\Imovel_101" ^
  --source "E:\Imovel_102" ^
  --source "F:\Apartamento_220" ^
  --collection-set "Trabalhos 16-07-2026" ^
  --recursive
```

Com nomes de coleção personalizados:

```bat
lrautomatic import ^
  --source "E:\Imovel_101|Casa 101" ^
  --source "E:\Imovel_102|Casa 102"
```

Por JSON:

```bat
lrautomatic import --job examples\import-job.json
```

Consultar:

```bat
lrautomatic jobs
lrautomatic status JOB_ID
```

## API

```http
POST /api/v1/import-jobs
Authorization: Bearer SUA_CHAVE
Content-Type: application/json
```

```json
{
  "collection_set": "Trabalhos 16-07-2026",
  "recursive": true,
  "sources": [
    {"path": "E:\\Imovel_101", "collection": "Casa 101"},
    {"path": "E:\\Imovel_102", "collection": "Casa 102"}
  ]
}
```

Endpoints:

- `GET /health`
- `POST /api/v1/import-jobs`
- `GET /api/v1/import-jobs`
- `GET /api/v1/import-jobs/{job_id}`
- `POST /api/v1/import-jobs/{job_id}/cancel`

## Catálogo-modelo

Quando você colocar um catálogo vazio criado pelo próprio Lightroom, configure:

```json
{
  "catalog_template": "D:\\Modelos\\CatalogoModelo.lrcat",
  "catalog_output_root": "D:\\Catalogos"
}
```

O comando reservado será:

```bat
lrautomatic catalog-create --name "Imovel 582"
```

Nesta primeira versão, ele copia o catálogo-modelo com segurança. A abertura/troca automática do Lightroom será adicionada após validar o caminho e a versão do executável na sua máquina.

## Limites atuais importantes

- O plugin importa no **catálogo atualmente aberto**.
- O Lightroom aceita apenas um catálogo aberto por vez.
- Smart Preview oficial deve ser criado pelo próprio Lightroom. O SDK público não expõe uma chamada estável para isso; a flag é registrada como solicitação pendente.
- Não há escrita direta no SQLite `.lrcat`.

## Segurança

- API vinculada somente a `127.0.0.1` por padrão.
- Chave Bearer obrigatória nos endpoints de alteração/consulta.
- Escrita atômica dos arquivos JSON.
- O plugin só lê tarefas da pasta local configurada.
