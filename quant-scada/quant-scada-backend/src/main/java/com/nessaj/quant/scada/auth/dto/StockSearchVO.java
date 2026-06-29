package com.nessaj.quant.scada.auth.dto;

import lombok.Data;

@Data
public class StockSearchVO {
    private String code;
    private String name;
    private String market;
    private String subMarket;
    private String sector;
}
