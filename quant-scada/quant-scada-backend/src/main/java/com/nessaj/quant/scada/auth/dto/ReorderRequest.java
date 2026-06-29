package com.nessaj.quant.scada.auth.dto;

import lombok.Data;

import java.util.List;

@Data
public class ReorderRequest {
    private List<ReorderItem> orders;

    @Data
    public static class ReorderItem {
        private Long id;
        private Integer sortOrder;
    }
}
