from io import BytesIO
from datetime import date
import torch
from pathlib import Path

from bottabot.config import settings

# Docling
from docling.datamodel.base_models import InputFormat, DocumentStream, NodeItem
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions, 
    AcceleratorOptions, 
    AcceleratorDevice,
    PictureDescriptionApiOptions # 이미지 캡션 생성
)
from docling.document_converter import DocumentConverter, PdfFormatOption, WordFormatOption, PowerpointFormatOption, ExcelFormatOption
from docling.datamodel.document import PictureItem, TableItem


# Docling 문서 분석기
class DoclingParser:
    parsed_doc_items: list[tuple[NodeItem, int]] = []
    parsed_markdown = ""
    metadata = {}

    def __init__(self, stream: BytesIO, file_name: str) -> None:
        # 파일 스트림 & 이름 저장
        self.stream = stream
        self.file_name = file_name
        self.file_type = Path(file_name).suffix

        # 파이프라인 옵션
        self.pipeline_options = PdfPipelineOptions()
        # OCR 사용 X
        self.pipeline_options.do_ocr = False
        # 테이블 구조화 O
        self.pipeline_options.do_table_structure = True
        # 이미지 생성 옵션 O
        self.pipeline_options.generate_picture_images = True
        self.pipeline_options.generate_table_images = True
        # 이미지 크기가 높은수록 해상도 좋음, VLM 분석 정확도 향상
        self.pipeline_options.images_scale = 3.0

        # 이미지 캡션 생성 옵션 O / 외부 서비스(Ollama) 이용
        self.pipeline_options.do_picture_description = True
        self.pipeline_options.enable_remote_services = True

        # 이미지 캡션 생성 API 구성 (Ollama)
        self.pipeline_options.picture_description_options = PictureDescriptionApiOptions(
            url=f"{settings.OLLAMA_URL}/v1/chat/completions",
            params={
                "model": settings.OLLAMA_CAPTION_VLM,
                "max_tokens": 300,
                "temperature": 0.0,
            },
            prompt=settings.IMAGE_CAPTION_INSTRUCTIONS,
            timeout=90.0,
            concurrency=1  # Ollama는 보통 1이 안정적
        )


        if torch.cuda.is_available():
            # GPU 사용
            self.pipeline_options.accelerator_options = AcceleratorOptions(
                device=AcceleratorDevice.CUDA
            )
        
    # 파일 타입 검사 및 포맷 옵션 설정
        match self.file_type:
            case '.pdf':
                input_format = InputFormat.PDF
                format_option = PdfFormatOption(pipeline_options=self.pipeline_options)

            case '.docx':
                input_format = InputFormat.DOCX
                format_option = WordFormatOption(pipeline_options=self.pipeline_options)

            case '.pptx':
                input_format = InputFormat.PPTX
                format_option = PowerpointFormatOption(pipeline_options=self.pipeline_options)

            case '.xlsx':
                input_format = InputFormat.XLSX
                format_option = ExcelFormatOption(pipeline_options=self.pipeline_options)
            
            case _:
                raise Exception(f"지원하지 않는 파일 형식: {self.file_type}")

        # 변환기 생성
        self.converter = DocumentConverter(
            format_options={input_format: format_option}
        )

    # 문서 분석
    def process_file(self):
        try:
            # BytesIO -> DocumentStream으로 래핑
            ds = DocumentStream(name=self.file_name, stream=self.stream)
            
            # 문서 분석 수행
            result = self.converter.convert(ds)
            self.parsed_doc_items = list(result.document.iterate_items())
            self.parsed_markdown = result.document.export_to_markdown()

            # 메타데이터 저장
            self.metadata = {
                'type': 'file',
                'name': str(self.file_name),
                'date': date.today().strftime('%Y-%m-%d')
            }
        
        except Exception as e:
            print(f"문서 분석 실패: {e}")
