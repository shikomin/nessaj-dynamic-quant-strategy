package com.nessaj.quant.scada.auth.dto;

import lombok.Data;

import javax.validation.constraints.NotBlank;

@Data
public class AddWatchlistRequest {
    @NotBlank(message = "股票代码不能为空")
    private String stockCode;
}
