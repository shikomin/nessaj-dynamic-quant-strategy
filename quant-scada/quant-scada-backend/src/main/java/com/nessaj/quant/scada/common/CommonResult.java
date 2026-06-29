package com.nessaj.quant.scada.common;

import lombok.Data;

@Data
public class CommonResult {
    private int code;
    private String msg;
    private Object data;

    public CommonResult() {}

    public CommonResult(int code, String msg, Object data) {
        this.code = code;
        this.msg = msg;
        this.data = data;
    }

    public static CommonResult ok() {
        return new CommonResult(200, "success", null);
    }

    public static CommonResult ok(Object data) {
        return new CommonResult(200, "success", data);
    }

    public static CommonResult error(String msg) {
        return new CommonResult(500, msg, null);
    }

    public static CommonResult error(int code, String msg) {
        return new CommonResult(code, msg, null);
    }
}
