import logging

from fleet.pipeline import run_pipeline


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def main() -> None:
    report = run_pipeline()
    logging.info("Run completed. Output folder: %s", report["output_dir"])


if __name__ == "__main__":
    main()
