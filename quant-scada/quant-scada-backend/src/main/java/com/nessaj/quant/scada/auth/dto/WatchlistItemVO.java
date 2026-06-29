package com.nessaj.quant.scada.auth.dto;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class WatchlistItemVO {
    private Long id;
    private Long userId;
    private String stockCode;
    private String stockName;
    private String market;
    private String subMarket;
    private String sector;
    private Integer sortOrder;
    private LocalDateTime createdAt;
}
