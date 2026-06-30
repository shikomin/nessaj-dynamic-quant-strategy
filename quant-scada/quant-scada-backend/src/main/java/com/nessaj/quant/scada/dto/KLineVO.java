package com.nessaj.quant.scada.dto;

import lombok.Data;

@Data
public class KLineVO {
    private String ts;
    private double open;
    private double high;
    private double low;
    private double close;
    private long volume;
    private double amount;
}
