from __future__ import annotations

import json
from pathlib import Path

import httpx
import typer
import uvicorn

from .api import create_app
from .catalogs import create_catalog
from .config import load_settings
from .diagnostics import create_diagnostic_zip
from .models import ImportJobRequest, ImportSource

app = typer.Typer(no_args_is_help=True, help="Automação local do Lightroom Classic")


def _client(config: str) -> tuple[httpx.Client, dict[str, str]]:
    settings = load_settings(config)
    client = httpx.Client(base_url=f"http://{settings.host}:{settings.port}", timeout=60)
    return client, {"Authorization": f"Bearer {settings.api_key}"}


@app.command()
def serve(config: str = typer.Option("config.json", help="Arquivo de configuração")) -> None:
    settings = load_settings(config)
    uvicorn.run(create_app(config), host=settings.host, port=settings.port)


@app.command("desktop")
def desktop() -> None:
    from .desktop import main
    main()


@app.command("import")
def import_photos(
    source: list[str] = typer.Option(None, "--source", "-s", help="Pasta ou Pasta|Nome da coleção"),
    job: Path | None = typer.Option(None, "--job", exists=True, dir_okay=False),
    collection_set: str | None = typer.Option(None),
    recursive: bool = typer.Option(False),
    smart_previews: bool = typer.Option(False, help="Seleciona as importadas e pede ao próprio Lightroom para criar Smart Previews"),
    config: str = typer.Option("config.json"),
) -> None:
    if job:
        request = ImportJobRequest.model_validate_json(job.read_text(encoding="utf-8"))
    else:
        if not source:
            raise typer.BadParameter("Informe ao menos um --source ou --job")
        sources: list[ImportSource] = []
        for item in source:
            path_text, separator, collection = item.partition("|")
            sources.append(ImportSource(path=path_text, collection=collection if separator else None))
        request = ImportJobRequest(
            sources=sources,
            collection_set=collection_set,
            recursive=recursive,
            build_smart_previews=smart_previews,
        )

    client, headers = _client(config)
    with client:
        response = client.post("/api/v1/import-jobs", json=request.model_dump(mode="json"), headers=headers)
        response.raise_for_status()
        payload = response.json()
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command()
def jobs(config: str = typer.Option("config.json")) -> None:
    client, headers = _client(config)
    with client:
        response = client.get("/api/v1/import-jobs", headers=headers)
        response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2))


@app.command()
def status(job_id: str, config: str = typer.Option("config.json")) -> None:
    client, headers = _client(config)
    with client:
        response = client.get(f"/api/v1/import-jobs/{job_id}", headers=headers)
        response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2))


@app.command()
def cancel(job_id: str, config: str = typer.Option("config.json")) -> None:
    client, headers = _client(config)
    with client:
        response = client.post(f"/api/v1/import-jobs/{job_id}/cancel", headers=headers)
        response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2))


@app.command("catalog-create")
def catalog_create(
    name: str = typer.Option(..., help="Nome do novo catálogo"),
    open_lightroom: bool = typer.Option(True, "--open-lightroom/--no-open-lightroom"),
    config: str = typer.Option("config.json"),
) -> None:
    result = create_catalog(load_settings(config), name, open_lightroom=open_lightroom)
    typer.echo(json.dumps({"catalog_path": str(result.catalog_path), "launched": result.launched}, ensure_ascii=False, indent=2))


@app.command("diagnostic-zip")
def diagnostic_zip(
    output: Path = typer.Option(Path.cwd(), help="Pasta de destino"),
    config: str = typer.Option("config.json"),
) -> None:
    path = create_diagnostic_zip(load_settings(config), config, output)
    typer.echo(str(path))


if __name__ == "__main__":
    app()
