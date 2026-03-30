"""
工具集接口
"""
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException

from app.schemas import ResponseModel
from app.schemas.tools import FormulaOCRResponse
from app.services.tools import simpletex_service
from app.utils.dependencies import get_current_user

router = APIRouter(prefix="/tools", tags=["工具集"])


@router.post("/formula-ocr", response_model=ResponseModel[FormulaOCRResponse])
async def formula_ocr(
    file: UploadFile = File(..., description="图片文件"),
    current_user=Depends(get_current_user),
):
    """
    公式识别（文件上传）

    支持格式：png, jpg, jpeg
    """
    # 检查文件类型
    allowed_types = ["image/png", "image/jpeg", "image/jpg"]
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail="不支持的文件格式，请上传 png 或 jpg 图片"
        )

    # 读取文件
    image_data = await file.read()

    # 确定图片格式
    image_format = file.filename.split(".")[-1].lower() if file.filename else "png"

    try:
        result = await simpletex_service.latex_ocr(image_data, image_format)

        if result.get("status"):
            return ResponseModel(
                data=FormulaOCRResponse(
                    status=True,
                    res=result.get("res"),
                    latex=result.get("res", {}).get("latex", "") if result.get("res") else "",
                )
            )
        else:
            return ResponseModel(
                code=500,
                message="识别失败",
                data=FormulaOCRResponse(
                    status=False,
                    error=result.get("msg", "未知错误"),
                )
            )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"识别服务异常: {str(e)}"
        )


@router.post("/formula-ocr-base64", response_model=ResponseModel[FormulaOCRResponse])
async def formula_ocr_base64(
    image_base64: str,
    current_user=Depends(get_current_user),
):
    """
    公式识别（Base64）

    直接传入Base64编码的图片字符串
    """
    try:
        result = await simpletex_service.latex_ocr_base64(image_base64)

        if result.get("status"):
            return ResponseModel(
                data=FormulaOCRResponse(
                    status=True,
                    res=result.get("res"),
                    latex=result.get("res", {}).get("latex", "") if result.get("res") else "",
                )
            )
        else:
            return ResponseModel(
                code=500,
                message="识别失败",
                data=FormulaOCRResponse(
                    status=False,
                    error=result.get("msg", "未知错误"),
                )
            )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"识别服务异常: {str(e)}"
        )
