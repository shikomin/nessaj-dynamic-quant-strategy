package com.nessaj.quant.scada.common;

import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(RuntimeException.class)
    public CommonResult handleRuntimeException(RuntimeException e) {
        return CommonResult.error(e.getMessage());
    }

    @ExceptionHandler(Exception.class)
    public CommonResult handleException(Exception e) {
        return CommonResult.error("服务器内部错误");
    }
}
