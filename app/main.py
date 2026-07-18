from fastapi import FastAPI


def create_app() -> FastAPI:
    application = FastAPI(title="Nasiya")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
